import hashlib
import io
import json
import os
import random
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from functools import wraps

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from flask import Flask, Response, jsonify, request, send_file, session
from flask_cors import CORS
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import MinMaxScaler

try:
    import kagglehub
    from kagglehub import KaggleDatasetAdapter
    KAGGLE_AVAILABLE = True
except ImportError:
    KAGGLE_AVAILABLE = False

try:
    #  Use this instead (clears the warning and brings back auto-complete)
   import tensorflow as tf
   from keras import layers, models, optimizers    

   TF_AVAILABLE = True
except ImportError:
    TF_AVAILABLE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "traffic_analytics.db")
ROOT_DIR = os.path.dirname(BASE_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
CSV_PATH = os.path.join(DATA_DIR, "traffic_volume.csv")
VIZ_PATH = os.path.join(DATA_DIR, "traffic_visualization.png")
KAGGLE_DATASET = "fedesoriano/traffic-prediction-dataset"

DATASET_STATE = {
    "rows": 0,
    "source": "local_csv",
    "columns": ["ID", "traffic_volume"],
    "stats": {},
    "last_sync": None,
}

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "traffic-cyber-secret-key-2026")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
CORS(app, supports_credentials=True, origins=["*"])

MODEL_STATE = {
    "gbm_model": None,
    "ann_model": None,
    "cnn_model": None,
    "scaler": None,
    "model_type": "Ensemble: GBM + ANN + CNN",
    "mse": 0.0,
    "r2": 0.0,
    "ann_mse": 0.0,
    "cnn_mse": 0.0,
    "epoch_loss": [],
    "ann_epoch_loss": [],
    "cnn_epoch_loss": [],
    "training_rows": 0,
    "last_trained": None,
    "feature_names": [
        "hour",
        "day_of_week",
        "temp",
        "precipitation",
        "historical_count",
        "rolling_mean_3",
        "rolling_std_3",
    ],
    "csv_rows": 0,
    "dataset_source": "local_csv",
}

BLOCKCHAIN_PATH = os.path.join(BASE_DIR, "blockchain_ledger.json")
CAMERA_LOCK = threading.Lock()
CAMERA = {"capture": None, "use_synthetic": True, "vehicle_count": 0}

INTERSECTIONS = [
    {"id": "INT-01", "name": "Central Plaza", "lat": 28.6139, "lng": 77.2090},
    {"id": "INT-02", "name": "Tech Park Gate", "lat": 28.6289, "lng": 77.2065},
    {"id": "INT-03", "name": "Metro Junction", "lat": 28.6200, "lng": 77.2300},
    {"id": "INT-04", "name": "Harbor Ring", "lat": 28.6080, "lng": 77.2500},
    {"id": "INT-05", "name": "University Ave", "lat": 28.6350, "lng": 77.1950},
    {"id": "INT-06", "name": "Industrial Belt", "lat": 28.6000, "lng": 77.1800},
]

DAY_NAMES = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


class TrafficBlockchain:
    def __init__(self):
        self.difficulty = 2
        self.chain = []
        self.load_chain()

    def compute_hash(self, index_num, timestamp, data_type, payload, previous_hash, nonce):
        block_string = json.dumps(
            {
                "index": index_num,
                "timestamp": timestamp,
                "data_type": data_type,
                "payload": payload,
                "previous_hash": previous_hash,
                "nonce": nonce,
            },
            sort_keys=True,
        )
        return hashlib.sha256(block_string.encode("utf-8")).hexdigest()

    
    def load_chain(self):
        conn = get_db()
        
        # --- ADD THIS BLOCK HERE ---
        # This guarantees the table exists before we try to read from it,
        # completely bypassing any order-of-execution issues!
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blockchain_blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                index_num INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                data_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                hash TEXT NOT NULL UNIQUE,
                nonce INTEGER DEFAULT 0
            )
            """
        )
        conn.commit()
        # ----------------------------

        rows = conn.execute(
            "SELECT index_num, timestamp, data_type, payload, previous_hash, hash, nonce FROM blockchain_blocks ORDER BY index_num ASC"
        ).fetchall()
        conn.close()
        
        if rows:
            self.chain = [dict(row) for row in rows]
            return
        genesis = self.create_genesis_block()
        self.chain = [genesis]
        self.persist_block(genesis)

    
    def create_genesis_block(self):
        timestamp = datetime.utcnow().isoformat() + "Z"
        payload = {"message": "TrafficNexus Genesis Block", "system": "Smart Traffic Volume System"}
        previous_hash = "0" * 64
        
        # --- FIX: Unpack BOTH the hash and the nonce here ---
        block_hash, nonce = self.proof_of_work(0, timestamp, "genesis", payload, previous_hash)
        
        return {
            "index_num": 0,
            "timestamp": timestamp,
            "data_type": "genesis",
            "payload": json.dumps(payload),
            "previous_hash": previous_hash,
            "hash": block_hash,
            "nonce": nonce,  # This will now use the correct unpacked integer
        }

    def proof_of_work(self, index_num, timestamp, data_type, payload, previous_hash):
        nonce = 0
        payload_value = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True)
        while True:
            block_hash = self.compute_hash(
                index_num, timestamp, data_type, payload_value, previous_hash, nonce
            )
            if block_hash.startswith("0" * self.difficulty):
                return block_hash, nonce
            nonce += 1

    def persist_block(self, block):
        conn = get_db()
        conn.execute(
            """
            INSERT INTO blockchain_blocks (index_num, timestamp, data_type, payload, previous_hash, hash, nonce)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                block["index_num"],
                block["timestamp"],
                block["data_type"],
                block["payload"] if isinstance(block["payload"], str) else json.dumps(block["payload"]),
                block["previous_hash"],
                block["hash"],
                block.get("nonce", 0),
            ),
        )
        conn.commit()
        conn.close()
        with open(BLOCKCHAIN_PATH, "w", encoding="utf-8") as ledger_file:
            json.dump(self.chain, ledger_file, indent=2)

    def add_public_record(self, data_type, payload):
        previous_block = self.chain[-1]
        index_num = previous_block["index_num"] + 1
        timestamp = datetime.utcnow().isoformat() + "Z"
        payload_str = json.dumps(payload, sort_keys=True)
        nonce = 0
        block_hash = self.proof_of_work(
            index_num, timestamp, data_type, payload, previous_block["hash"]
        )
        block = {
            "index_num": index_num,
            "timestamp": timestamp,
            "data_type": data_type,
            "payload": payload_str,
            "previous_hash": previous_block["hash"],
            "hash": block_hash,
            "nonce": nonce,
        }
        self.chain.append(block)
        self.persist_block(block)
        return block

    def verify_chain(self):
        for index in range(1, len(self.chain)):
            current = self.chain[index]
            previous = self.chain[index - 1]
            if current["previous_hash"] != previous["hash"]:
                return False, index
            recomputed = self.compute_hash(
                current["index_num"],
                current["timestamp"],
                current["data_type"],
                current["payload"],
                current["previous_hash"],
                current.get("nonce", 0),
            )
            if recomputed != current["hash"]:
                return False, index
        return True, len(self.chain)

    def get_recent_blocks(self, limit=10):
        return self.chain[-limit:]


TRAFFIC_CHAIN = TrafficBlockchain()


def init_database():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            oauth_provider TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS traffic_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            intersection_id TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            hour INTEGER NOT NULL,
            day_of_week INTEGER NOT NULL,
            temp REAL NOT NULL,
            precipitation REAL NOT NULL,
            historical_count INTEGER NOT NULL,
            congestion_level REAL NOT NULL,
            two_wheelers INTEGER DEFAULT 0,
            commuter_cars INTEGER DEFAULT 0,
            ev_autonomous INTEGER DEFAULT 0,
            freight_trucks INTEGER DEFAULT 0,
            block_hash TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS blockchain_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_num INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            data_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            hash TEXT NOT NULL UNIQUE,
            nonce INTEGER DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()


def generate_synthetic_dataset(count=1200):
    conn = get_db()
    cursor = conn.cursor()
    existing = cursor.execute("SELECT COUNT(*) FROM traffic_history").fetchone()[0]
    if existing >= 500:
        conn.close()
        return existing

    rows = []
    base_date = datetime.now() - timedelta(days=90)
    for i in range(count):
        recorded = base_date + timedelta(hours=i * 2)
        hour = recorded.hour
        day_of_week = recorded.weekday()
        intersection = random.choice(INTERSECTIONS)
        rain_factor = random.uniform(0.0, 1.0) if random.random() < 0.25 else random.uniform(0.0, 0.15)
        temp = random.uniform(18.0, 42.0) - (rain_factor * 8.0)
        rush_multiplier = 1.0
        if 7 <= hour <= 10:
            rush_multiplier = 1.8
        elif 17 <= hour <= 20:
            rush_multiplier = 2.1
        elif 12 <= hour <= 14:
            rush_multiplier = 1.3
        if day_of_week >= 5:
            rush_multiplier *= 0.75
        base_count = int(random.randint(80, 220) * rush_multiplier)
        historical_count = base_count + int(rain_factor * 60)
        congestion = min(
            1.0,
            max(
                0.05,
                (historical_count / 400.0)
                + (rain_factor * 0.25)
                + (0.15 if 7 <= hour <= 10 or 17 <= hour <= 20 else 0.0)
                + random.uniform(-0.05, 0.08),
            ),
        )
        fleet_total = historical_count
        two_wheelers = int(fleet_total * random.uniform(0.28, 0.42))
        commuter_cars = int(fleet_total * random.uniform(0.30, 0.45))
        ev_autonomous = int(fleet_total * random.uniform(0.08, 0.18))
        freight_trucks = max(0, fleet_total - two_wheelers - commuter_cars - ev_autonomous)
        rows.append(
            (
                intersection["id"],
                recorded.isoformat(),
                hour,
                day_of_week,
                round(temp, 2),
                round(rain_factor, 3),
                historical_count,
                round(congestion, 4),
                two_wheelers,
                commuter_cars,
                ev_autonomous,
                freight_trucks,
            )
        )

    cursor.executemany(
        """
        INSERT INTO traffic_history (
            intersection_id, recorded_at, hour, day_of_week, temp, precipitation,
            historical_count, congestion_level, two_wheelers, commuter_cars,
            ev_autonomous, freight_trucks
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    total = cursor.execute("SELECT COUNT(*) FROM traffic_history").fetchone()[0]
    conn.close()
    return total


def load_csv_dataframe():
    if not os.path.exists(CSV_PATH):
        return None
    df = pd.read_csv(CSV_PATH)
    if "ID" not in df.columns or "traffic_volume" not in df.columns:
        return None
    df = df.sort_values("ID").reset_index(drop=True)
    return df


def enrich_csv_features(df):
    enriched = df.copy()
    enriched["hour"] = enriched["ID"] % 24
    enriched["day_of_week"] = (enriched["ID"] // 24) % 7
    enriched["temp"] = 22.0 + 8.0 * np.sin(enriched["hour"] * np.pi / 12.0)
    enriched["precipitation"] = np.where(
        enriched["traffic_volume"] < enriched["traffic_volume"].quantile(0.15),
        np.random.uniform(0.4, 0.9, len(enriched)),
        np.random.uniform(0.0, 0.12, len(enriched)),
    )
    enriched["historical_count"] = enriched["traffic_volume"].astype(float)
    volume_max = enriched["traffic_volume"].max()
    enriched["congestion_level"] = (enriched["traffic_volume"] / volume_max).clip(0.05, 1.0)
    enriched["rolling_mean_3"] = enriched["traffic_volume"].rolling(window=3, min_periods=1).mean()
    enriched["rolling_std_3"] = enriched["traffic_volume"].rolling(window=3, min_periods=1).std().fillna(0)
    return enriched


def sync_kaggle_dataset(file_path=""):
    if not KAGGLE_AVAILABLE:
        raise RuntimeError("kagglehub not installed. Run: pip install kagglehub[pandas-datasets]")
    df = kagglehub.load_dataset(
        KaggleDatasetAdapter.PANDAS,
        KAGGLE_DATASET,
        file_path,
    )
    if "ID" not in df.columns:
        df = df.reset_index().rename(columns={"index": "ID"})
    if "traffic_volume" not in df.columns:
        volume_cols = [c for c in df.columns if "volume" in c.lower() or "traffic" in c.lower()]
        if volume_cols:
            df = df.rename(columns={volume_cols[0]: "traffic_volume"})
        else:
            numeric_cols = df.select_dtypes(include="number").columns.tolist()
            df["traffic_volume"] = df[numeric_cols[-1]]
    df = df[["ID", "traffic_volume"]].copy()
    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(CSV_PATH, index=False)
    DATASET_STATE["source"] = "kaggle"
    DATASET_STATE["last_sync"] = datetime.utcnow().isoformat() + "Z"
    refresh_dataset_metadata()
    generate_matplotlib_visualization()
    return df


def refresh_dataset_metadata():
    df = load_csv_dataframe()
    if df is None:
        DATASET_STATE["rows"] = 0
        DATASET_STATE["stats"] = {}
        return None
    stats = df["traffic_volume"].describe().to_dict()
    DATASET_STATE["rows"] = int(len(df))
    DATASET_STATE["stats"] = {key: round(float(value), 4) for key, value in stats.items()}
    DATASET_STATE["columns"] = list(df.columns)
    return df


def generate_matplotlib_visualization():
    df = load_csv_dataframe()
    if df is None or df.empty:
        return False
    os.makedirs(DATA_DIR, exist_ok=True)
    plt.style.use("dark_background")
    figure, axes = plt.subplots(2, 1, figsize=(12, 8), facecolor="#060913")
    figure.patch.set_facecolor("#060913")

    axes[0].plot(df["ID"], df["traffic_volume"], color="#00f2fe", linewidth=1.2, label="Traffic Volume")
    axes[0].fill_between(df["ID"], df["traffic_volume"], alpha=0.15, color="#00f2fe")
    axes[0].set_title("Traffic Volume Time Series (Kaggle CSV Dataset)", color="#e8f4ff", fontsize=12)
    axes[0].set_xlabel("Record ID", color="#8ba3c7")
    axes[0].set_ylabel("Volume", color="#8ba3c7")
    axes[0].grid(True, alpha=0.15)
    axes[0].legend(loc="upper right")

    axes[1].hist(df["traffic_volume"], bins=30, color="#9b51e0", edgecolor="#00f2fe", alpha=0.85)
    axes[1].set_title("Traffic Volume Distribution", color="#e8f4ff", fontsize=12)
    axes[1].set_xlabel("Volume", color="#8ba3c7")
    axes[1].set_ylabel("Frequency", color="#8ba3c7")
    axes[1].grid(True, alpha=0.15)

    plt.tight_layout()
    figure.savefig(VIZ_PATH, dpi=120, facecolor=figure.get_facecolor())
    plt.close(figure)
    return True


def load_csv_training_matrix():
    df = load_csv_dataframe()
    if df is None or len(df) < 20:
        return None, None
    enriched = enrich_csv_features(df)
    feature_cols = [
        "hour",
        "day_of_week",
        "temp",
        "precipitation",
        "historical_count",
        "rolling_mean_3",
        "rolling_std_3",
    ]
    x_data = enriched[feature_cols].values.astype(float)
    y_data = enriched["congestion_level"].values.astype(float)
    return x_data, y_data


def load_training_matrix():
    csv_x, csv_y = load_csv_training_matrix()
    conn = get_db()
    cursor = conn.cursor()
    rows = cursor.execute(
        """
        SELECT hour, day_of_week, temp, precipitation, historical_count, congestion_level
        FROM traffic_history
        ORDER BY recorded_at DESC
        LIMIT 5000
        """
    ).fetchall()
    conn.close()

    sql_x, sql_y = None, None
    if rows:
        sql_x = []
        sql_y = []
        for row in rows:
            sql_x.append(
                [
                    float(row["hour"]),
                    float(row["day_of_week"]),
                    float(row["temp"]),
                    float(row["precipitation"]),
                    float(row["historical_count"]),
                    float(row["historical_count"]),
                    0.0,
                ]
            )
            sql_y.append(float(row["congestion_level"]))
        sql_x = np.array(sql_x)
        sql_y = np.array(sql_y)

    if csv_x is not None and sql_x is not None:
        x_matrix = np.vstack([csv_x, sql_x])
        y_vector = np.concatenate([csv_y, sql_y])
        return x_matrix, y_vector
    if csv_x is not None:
        return csv_x, csv_y
    if sql_x is not None:
        return sql_x, sql_y
    return None, None


def build_cnn_model(input_shape):
    if not TF_AVAILABLE:
        return None
    cnn = models.Sequential(
        [
            layers.Input(shape=input_shape),
            layers.Conv1D(32, kernel_size=2, activation="relu", padding="same"),
            layers.BatchNormalization(),
            layers.Conv1D(64, kernel_size=2, activation="relu", padding="same"),
            layers.MaxPooling1D(pool_size=2),
            layers.Flatten(),
            layers.Dense(64, activation="relu"),
            layers.Dropout(0.2),
            layers.Dense(32, activation="relu"),
            layers.Dense(1, activation="sigmoid"),
        ]
    )
    cnn.compile(
        optimizer=optimizers.Adam(learning_rate=0.001),
        loss="mse",
        metrics=["mae"],
    )
    return cnn


def train_all_models():
    x_matrix, y_vector = load_training_matrix()
    if x_matrix is None or len(x_matrix) < 50:
        x_matrix = np.array(
            [
                [
                    random.randint(0, 23),
                    random.randint(0, 6),
                    random.uniform(15, 40),
                    random.uniform(0, 1),
                    random.randint(50, 350),
                    random.randint(50, 350),
                    random.uniform(0, 50),
                ]
                for _ in range(600)
            ]
        )
        y_vector = np.array(
            [
                min(
                    1.0,
                    max(
                        0.05,
                        (row[4] / 400.0) + (row[3] * 0.2) + random.uniform(-0.05, 0.05),
                    ),
                )
                for row in x_matrix
            ]
        )

    scaler = MinMaxScaler()
    x_scaled = scaler.fit_transform(x_matrix)
    x_train, x_test, y_train, y_test = train_test_split(
        x_scaled, y_vector, test_size=0.2, random_state=42
    )

    gbm = GradientBoostingRegressor(
        n_estimators=120,
        learning_rate=0.08,
        max_depth=5,
        random_state=42,
    )
    gbm.fit(x_train, y_train)
    gbm_predictions = gbm.predict(x_test)
    gbm_mse = float(mean_squared_error(y_test, gbm_predictions))

    epoch_losses = []
    staged_model = GradientBoostingRegressor(
        n_estimators=120,
        learning_rate=0.08,
        max_depth=5,
        random_state=42,
    )
    staged_model.fit(x_train, y_train)
    step = max(1, 120 // 12)
    for index, prediction_batch in enumerate(staged_model.staged_predict(x_test)):
        if index % step == 0 or index == 119:
            epoch_losses.append(round(float(mean_squared_error(y_test, prediction_batch)), 6))

    ann = MLPRegressor(
        hidden_layer_sizes=(128, 64, 32),
        activation="relu",
        solver="adam",
        learning_rate_init=0.001,
        max_iter=400,
        random_state=42,
        early_stopping=True,
        validation_fraction=0.15,
    )
    ann.fit(x_train, y_train)
    ann_predictions = ann.predict(x_test)
    ann_mse = float(mean_squared_error(y_test, ann_predictions))
    ann_loss_curve = []
    if hasattr(ann, "loss_curve_") and ann.loss_curve_:
        step_ann = max(1, len(ann.loss_curve_) // 12)
        ann_loss_curve = [
            round(float(value), 6)
            for idx, value in enumerate(ann.loss_curve_)
            if idx % step_ann == 0
        ]

    cnn_model = None
    cnn_mse = ann_mse
    cnn_loss_curve = []
    if TF_AVAILABLE:
        x_cnn_train = x_train.reshape((x_train.shape[0], x_train.shape[1], 1))
        x_cnn_test = x_test.reshape((x_test.shape[0], x_test.shape[1], 1))
        cnn_model = build_cnn_model((x_train.shape[1], 1))
        history = cnn_model.fit(
            x_cnn_train,
            y_train,
            epochs=40,
            batch_size=32,
            validation_split=0.15,
            verbose=0,
        )
        cnn_predictions = cnn_model.predict(x_cnn_test, verbose=0).flatten()
        cnn_mse = float(mean_squared_error(y_test, cnn_predictions))
        cnn_loss_curve = [round(float(v), 6) for v in history.history.get("loss", [])[::3]]

    ensemble_predictions = (gbm_predictions + ann_predictions) / 2.0
    if TF_AVAILABLE and cnn_model is not None:
        cnn_pred = cnn_model.predict(
            x_test.reshape((x_test.shape[0], x_test.shape[1], 1)), verbose=0
        ).flatten()
        ensemble_predictions = (gbm_predictions + ann_predictions + cnn_pred) / 3.0

    ensemble_mse = float(mean_squared_error(y_test, ensemble_predictions))
    ensemble_r2 = float(r2_score(y_test, ensemble_predictions))

    MODEL_STATE["gbm_model"] = gbm
    MODEL_STATE["ann_model"] = ann
    MODEL_STATE["cnn_model"] = cnn_model
    MODEL_STATE["scaler"] = scaler
    MODEL_STATE["mse"] = round(ensemble_mse, 6)
    MODEL_STATE["r2"] = round(ensemble_r2, 6)
    MODEL_STATE["ann_mse"] = round(ann_mse, 6)
    MODEL_STATE["cnn_mse"] = round(cnn_mse, 6)
    MODEL_STATE["epoch_loss"] = epoch_losses
    MODEL_STATE["ann_epoch_loss"] = ann_loss_curve
    MODEL_STATE["cnn_epoch_loss"] = cnn_loss_curve
    MODEL_STATE["training_rows"] = int(len(x_matrix))
    MODEL_STATE["last_trained"] = datetime.utcnow().isoformat() + "Z"
    MODEL_STATE["csv_rows"] = DATASET_STATE.get("rows", 0)
    MODEL_STATE["dataset_source"] = DATASET_STATE.get("source", "local_csv")
    return MODEL_STATE


def build_feature_vector(hour, day_of_week, temp, precipitation, historical_count):
    return np.array(
        [
            [
                float(hour),
                float(day_of_week),
                float(temp),
                float(precipitation),
                float(historical_count),
                float(historical_count),
                0.0,
            ]
        ]
    )


def train_model():
    return train_all_models()


def predict_congestion(hour, day_of_week, temp, precipitation, historical_count):
    feature_vector = build_feature_vector(hour, day_of_week, temp, precipitation, historical_count)
    scaler = MODEL_STATE.get("scaler")
    gbm = MODEL_STATE.get("gbm_model")
    ann = MODEL_STATE.get("ann_model")
    cnn = MODEL_STATE.get("cnn_model")

    if scaler is not None and gbm is not None and ann is not None:
        try:
            scaled = scaler.transform(feature_vector)
            gbm_pred = float(gbm.predict(scaled)[0])
            ann_pred = float(ann.predict(scaled)[0])
            predictions = [gbm_pred, ann_pred]
            if cnn is not None and TF_AVAILABLE:
                cnn_input = scaled.reshape((1, scaled.shape[1], 1))
                cnn_pred = float(cnn.predict(cnn_input, verbose=0)[0][0])
                predictions.append(cnn_pred)
            value = sum(predictions) / len(predictions)
            return round(min(1.0, max(0.0, value)), 4)
        except Exception:
            pass

    fallback = (
        (historical_count / 400.0)
        + (precipitation * 0.22)
        + (0.12 if 7 <= hour <= 10 or 17 <= hour <= 20 else 0.0)
    )
    return round(min(1.0, max(0.05, fallback)), 4)


def predict_congestion_breakdown(hour, day_of_week, temp, precipitation, historical_count):
    feature_vector = build_feature_vector(hour, day_of_week, temp, precipitation, historical_count)
    result = {
        "ensemble": predict_congestion(hour, day_of_week, temp, precipitation, historical_count),
        "gbm": None,
        "ann": None,
        "cnn": None,
    }
    scaler = MODEL_STATE.get("scaler")
    gbm = MODEL_STATE.get("gbm_model")
    ann = MODEL_STATE.get("ann_model")
    cnn = MODEL_STATE.get("cnn_model")
    if scaler is None:
        return result
    try:
        scaled = scaler.transform(feature_vector)
        if gbm is not None:
            result["gbm"] = round(float(gbm.predict(scaled)[0]), 4)
        if ann is not None:
            result["ann"] = round(float(ann.predict(scaled)[0]), 4)
        if cnn is not None and TF_AVAILABLE:
            cnn_input = scaled.reshape((1, scaled.shape[1], 1))
            result["cnn"] = round(float(cnn.predict(cnn_input, verbose=0)[0][0]), 4)
    except Exception:
        pass
    return result


def login_required(role=None):
    def decorator(handler):
        @wraps(handler)
        def wrapper(*args, **kwargs):
            user_id = session.get("user_id")
            if not user_id:
                return jsonify({"success": False, "message": "Authentication required"}), 401
            if role:
                conn = get_db()
                user = conn.execute(
                    "SELECT role FROM users WHERE id = ?", (user_id,)
                ).fetchone()
                conn.close()
                if not user or user["role"] != role:
                    return jsonify({"success": False, "message": "Admin access required"}), 403
            return handler(*args, **kwargs)

        return wrapper

    return decorator


def parse_nlp_text(text):
    lowered = text.lower().strip()
    result = {
        "hour": datetime.now().hour,
        "day_of_week": datetime.now().weekday(),
        "temp": 28.0,
        "precipitation": 0.0,
        "historical_count": 180,
        "is_raining": False,
        "target_date": datetime.now().strftime("%Y-%m-%d"),
        "confidence": 0.55,
        "parsed_tokens": [],
    }

    hour_match = re.search(r"(\d{1,2})\s*(?:pm|am|:\d{2})?", lowered)
    explicit_hour = re.search(r"at\s+(\d{1,2})\s*(am|pm)?", lowered)
    if explicit_hour:
        hour_value = int(explicit_hour.group(1))
        meridiem = explicit_hour.group(2)
        if meridiem == "pm" and hour_value < 12:
            hour_value += 12
        if meridiem == "am" and hour_value == 12:
            hour_value = 0
        result["hour"] = hour_value % 24
        result["parsed_tokens"].append(f"hour:{result['hour']}")
        result["confidence"] += 0.1

    for index, day_name in enumerate(DAY_NAMES):
        if day_name in lowered:
            result["day_of_week"] = index
            result["parsed_tokens"].append(f"day:{day_name}")
            result["confidence"] += 0.08
            break

    if "tomorrow" in lowered:
        target = datetime.now() + timedelta(days=1)
        result["day_of_week"] = target.weekday()
        result["target_date"] = target.strftime("%Y-%m-%d")
        result["parsed_tokens"].append("relative:tomorrow")
        result["confidence"] += 0.1

    if any(word in lowered for word in ["rain", "raining", "storm", "wet", "precipitation"]):
        result["precipitation"] = random.uniform(0.45, 0.95)
        result["is_raining"] = True
        result["temp"] = random.uniform(18.0, 24.0)
        result["parsed_tokens"].append("weather:rain")
        result["confidence"] += 0.12
    elif any(word in lowered for word in ["sunny", "clear", "dry", "hot"]):
        result["precipitation"] = random.uniform(0.0, 0.08)
        result["is_raining"] = False
        result["temp"] = random.uniform(30.0, 40.0)
        result["parsed_tokens"].append("weather:clear")
        result["confidence"] += 0.08

    if "peak" in lowered or "rush" in lowered:
        result["hour"] = 18
        result["historical_count"] = 320
        result["parsed_tokens"].append("pattern:peak")
        result["confidence"] += 0.07

    if "light" in lowered or "low traffic" in lowered:
        result["historical_count"] = 90
        result["parsed_tokens"].append("density:low")
        result["confidence"] += 0.05

    if "heavy" in lowered or "congestion" in lowered:
        result["historical_count"] = 340
        result["parsed_tokens"].append("density:high")
        result["confidence"] += 0.07

    result["confidence"] = round(min(0.98, result["confidence"]), 2)
    return result


@app.route("/api/auth/register", methods=["POST"])
def register():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    if len(username) < 3 or len(password) < 4 or "@" not in email:
        return jsonify({"success": False, "message": "Invalid registration fields"}), 400
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO users (username, email, password_hash, role, created_at)
            VALUES (?, ?, ?, 'user', ?)
            """,
            (username, email, hash_password(password), datetime.utcnow().isoformat()),
        )
        conn.commit()
        user = conn.execute(
            "SELECT id, username, email, role FROM users WHERE email = ?", (email,)
        ).fetchone()
        session["user_id"] = user["id"]
        return jsonify(
            {
                "success": True,
                "user": {
                    "id": user["id"],
                    "username": user["username"],
                    "email": user["email"],
                    "role": user["role"],
                },
            }
        )
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "message": "Username or email already exists"}), 409
    finally:
        conn.close()


@app.route("/api/auth/login", methods=["POST"])
def login():
    payload = request.get_json(silent=True) or {}
    identifier = (payload.get("identifier") or payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    conn = get_db()
    user = conn.execute(
        """
        SELECT id, username, email, role, password_hash
        FROM users
        WHERE email = ? OR username = ?
        """,
        (identifier, identifier),
    ).fetchone()
    conn.close()
    if not user or user["password_hash"] != hash_password(password):
        return jsonify({"success": False, "message": "Invalid credentials"}), 401
    session["user_id"] = user["id"]
    return jsonify(
        {
            "success": True,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "email": user["email"],
                "role": user["role"],
            },
        }
    )


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})


@app.route("/api/auth/session", methods=["GET"])
def auth_session():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"authenticated": False})
    conn = get_db()
    user = conn.execute(
        "SELECT id, username, email, role FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if not user:
        session.clear()
        return jsonify({"authenticated": False})
    return jsonify(
        {
            "authenticated": True,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "email": user["email"],
                "role": user["role"],
            },
        }
    )


@app.route("/api/auth/oauth-mock", methods=["POST"])
def oauth_mock():
    payload = request.get_json(silent=True) or {}
    provider = (payload.get("provider") or "").lower()
    if provider not in ("google", "github"):
        return jsonify({"success": False, "message": "Unsupported OAuth provider"}), 400

    config_name = (
        "auth_config_google.json" if provider == "google" else "auth_config_github.json"
    )
    config_path = os.path.join(ROOT_DIR, config_name)
    if not os.path.exists(config_path):
        return jsonify({"success": False, "message": "OAuth configuration missing"}), 500

    with open(config_path, "r", encoding="utf-8") as config_file:
        provider_config = json.load(config_file)

    mock_email = (
        f"cyber.{provider}.{uuid.uuid4().hex[:6]}@traffic-system.dev"
    )
    mock_username = f"{provider}_user_{uuid.uuid4().hex[:5]}"
    conn = get_db()
    existing = conn.execute(
        "SELECT id, username, email, role FROM users WHERE oauth_provider = ? AND email LIKE ?",
        (provider, f"%@{provider}-oauth.dev"),
    ).fetchone()
    if existing:
        user = existing
    else:
        conn.execute(
            """
            INSERT INTO users (username, email, password_hash, role, oauth_provider, created_at)
            VALUES (?, ?, ?, 'user', ?, ?)
            """,
            (
                mock_username,
                mock_email.replace(".dev", f"-oauth.dev"),
                hash_password(uuid.uuid4().hex),
                provider,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        user = conn.execute(
            "SELECT id, username, email, role FROM users WHERE email = ?",
            (mock_email.replace(".dev", f"-oauth.dev"),),
        ).fetchone()

    conn.close()
    session["user_id"] = user["id"]
    return jsonify(
        {
            "success": True,
            "provider": provider,
            "token": f"mock_{provider}_{uuid.uuid4().hex}",
            "config": provider_config,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "email": user["email"],
                "role": user["role"],
            },
        }
    )


@app.route("/api/nlp/parse", methods=["POST"])
def nlp_parse():
    payload = request.get_json(silent=True) or {}
    text = payload.get("text") or ""
    if not text.strip():
        return jsonify({"success": False, "message": "Command text required"}), 400
    parsed = parse_nlp_text(text)
    congestion = predict_congestion(
        parsed["hour"],
        parsed["day_of_week"],
        parsed["temp"],
        parsed["precipitation"],
        parsed["historical_count"],
    )
    return jsonify({"success": True, "parsed": parsed, "predicted_congestion": congestion})


@app.route("/api/predict/model", methods=["POST"])
def predict_model():
    payload = request.get_json(silent=True) or {}
    hour = int(payload.get("hour", datetime.now().hour))
    day_of_week = int(payload.get("day_of_week", datetime.now().weekday()))
    temp = float(payload.get("temp", 28.0))
    precipitation = float(payload.get("precipitation", 0.0))
    historical_count = int(payload.get("historical_count", 180))
    intersection_id = payload.get("intersection_id", "INT-01")

    congestion = predict_congestion(
        hour, day_of_week, temp, precipitation, historical_count
    )
    estimated_vehicles = int(historical_count * (0.85 + congestion * 0.4))
    return jsonify(
        {
            "success": True,
            "intersection_id": intersection_id,
            "inputs": {
                "hour": hour,
                "day_of_week": day_of_week,
                "temp": temp,
                "precipitation": precipitation,
                "historical_count": historical_count,
            },
            "predicted_congestion": congestion,
            "estimated_vehicle_count": estimated_vehicles,
            "congestion_label": (
                "Critical" if congestion > 0.75 else "Heavy" if congestion > 0.55 else "Moderate" if congestion > 0.35 else "Light"
            ),
        }
    )


@app.route("/api/traffic/live", methods=["GET"])
def traffic_live():
    intersection_id = request.args.get("intersection_id", "INT-01")
    intersection = next(
        (item for item in INTERSECTIONS if item["id"] == intersection_id),
        INTERSECTIONS[0],
    )
    hour = datetime.now().hour
    rush_factor = 1.6 if 7 <= hour <= 10 or 17 <= hour <= 20 else 1.0
    vehicle_count = int(random.randint(90, 260) * rush_factor)
    congestion = predict_congestion(
        hour,
        datetime.now().weekday(),
        random.uniform(22.0, 36.0),
        random.uniform(0.0, 0.3),
        vehicle_count,
    )
    density_color = "green"
    if congestion > 0.55:
        density_color = "red"
    elif congestion > 0.35:
        density_color = "yellow"

    fleet_total = vehicle_count
    two_wheelers = int(fleet_total * 0.35)
    commuter_cars = int(fleet_total * 0.38)
    ev_autonomous = int(fleet_total * 0.12)
    freight_trucks = max(0, fleet_total - two_wheelers - commuter_cars - ev_autonomous)

    return jsonify(
        {
            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "intersection": intersection,
            "vehicle_count": vehicle_count,
            "congestion_index": congestion,
            "density_color": density_color,
            "fleet": {
                "two_wheelers": two_wheelers,
                "commuter_cars": commuter_cars,
                "ev_autonomous": ev_autonomous,
                "freight_trucks": freight_trucks,
            },
            "intersections": [
                {
                    **item,
                    "congestion_index": round(
                        predict_congestion(
                            hour,
                            datetime.now().weekday(),
                            random.uniform(20, 35),
                            random.uniform(0, 0.2),
                            random.randint(80, 300),
                        ),
                        3,
                    ),
                    "density_color": density_color,
                }
                for item in INTERSECTIONS
            ],
        }
    )


@app.route("/api/traffic/history", methods=["GET"])
def traffic_history():
    day_of_week = request.args.get("day_of_week")
    intersection_id = request.args.get("intersection_id")
    conn = get_db()
    query = """
        SELECT hour, AVG(historical_count) as avg_count, AVG(congestion_level) as avg_congestion,
               AVG(two_wheelers) as two_wheelers, AVG(commuter_cars) as commuter_cars,
               AVG(ev_autonomous) as ev_autonomous, AVG(freight_trucks) as freight_trucks
        FROM traffic_history
        WHERE 1=1
    """
    params = []
    if day_of_week is not None:
        query += " AND day_of_week = ?"
        params.append(int(day_of_week))
    if intersection_id:
        query += " AND intersection_id = ?"
        params.append(intersection_id)
    query += " GROUP BY hour ORDER BY hour ASC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify(
        {
            "success": True,
            "records": [
                {
                    "hour": row["hour"],
                    "avg_count": round(row["avg_count"], 1),
                    "avg_congestion": round(row["avg_congestion"], 3),
                    "fleet": {
                        "two_wheelers": int(row["two_wheelers"]),
                        "commuter_cars": int(row["commuter_cars"]),
                        "ev_autonomous": int(row["ev_autonomous"]),
                        "freight_trucks": int(row["freight_trucks"]),
                    },
                }
                for row in rows
            ],
        }
    )


@app.route("/api/admin/metrics", methods=["GET"])
@login_required(role="admin")
def admin_metrics():
    conn = get_db()
    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    log_count = conn.execute("SELECT COUNT(*) FROM traffic_history").fetchone()[0]
    recent_users = conn.execute(
        "SELECT id, username, email, role, oauth_provider, created_at FROM users ORDER BY id DESC LIMIT 12"
    ).fetchall()
    recent_logs = conn.execute(
        """
        SELECT id, intersection_id, recorded_at, hour, historical_count, congestion_level
        FROM traffic_history ORDER BY id DESC LIMIT 15
        """
    ).fetchall()
    conn.close()
    return jsonify(
        {
            "success": True,
            "system_health": "Operational",
            "database": {
                "path": DB_PATH,
                "users": user_count,
                "traffic_logs": log_count,
            },
            "model": {
                "type": MODEL_STATE["model_type"],
                "mse": MODEL_STATE["mse"],
                "r2": MODEL_STATE["r2"],
                "epoch_loss": MODEL_STATE["epoch_loss"],
                "ann_mse": MODEL_STATE["ann_mse"],
                "cnn_mse": MODEL_STATE["cnn_mse"],
                "training_rows": MODEL_STATE["training_rows"],
                "last_trained": MODEL_STATE["last_trained"],
                "csv_rows": MODEL_STATE.get("csv_rows", 0),
                "dataset_source": MODEL_STATE.get("dataset_source", "local_csv"),
            },
            "dataset": DATASET_STATE,
            "users": [dict(row) for row in recent_users],
            "recent_logs": [dict(row) for row in recent_logs],
            "intersections": INTERSECTIONS,
        }
    )


@app.route("/api/admin/retrain", methods=["POST"])
@login_required(role="admin")
def admin_retrain():
    generate_synthetic_dataset(count=300)
    metrics = train_model()
    return jsonify(
        {
            "success": True,
            "message": "Model retrained on refreshed SQL logs",
            "model": {
                "type": metrics["model_type"],
                "mse": metrics["mse"],
                "r2": metrics["r2"],
                "epoch_loss": metrics["epoch_loss"],
                "training_rows": metrics["training_rows"],
                "last_trained": metrics["last_trained"],
            },
        }
    )


@app.route("/api/admin/promote", methods=["POST"])
def promote_admin():
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "admin@traffic.dev").strip().lower()
    conn = get_db()
    user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if user:
        conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (user["id"],))
    else:
        conn.execute(
            """
            INSERT INTO users (username, email, password_hash, role, created_at)
            VALUES (?, ?, ?, 'admin', ?)
            """,
            ("sysadmin", email, hash_password("admin123"), datetime.utcnow().isoformat()),
        )
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": f"Admin role configured for {email}"})


@app.route("/api/dataset/info", methods=["GET"])
def dataset_info():
    df = refresh_dataset_metadata()
    preview = []
    if df is not None:
        preview = df.head(10).to_dict(orient="records")
    return jsonify(
        {
            "success": True,
            "csv_path": CSV_PATH,
            "visualization_path": VIZ_PATH,
            "kaggle_dataset": KAGGLE_DATASET,
            "kaggle_available": KAGGLE_AVAILABLE,
            "rows": DATASET_STATE["rows"],
            "source": DATASET_STATE["source"],
            "columns": DATASET_STATE["columns"],
            "stats": DATASET_STATE["stats"],
            "last_sync": DATASET_STATE["last_sync"],
            "preview": preview,
        }
    )


@app.route("/api/dataset/preview", methods=["GET"])
def dataset_preview():
    df = load_csv_dataframe()
    if df is None:
        return jsonify({"success": False, "message": "CSV dataset not found"}), 404
    limit = int(request.args.get("limit", 20))
    return jsonify(
        {
            "success": True,
            "total_rows": len(df),
            "records": df.head(limit).to_dict(orient="records"),
            "describe": df.describe().to_dict(),
        }
    )


@app.route("/api/dataset/visualization", methods=["GET"])
def dataset_visualization():
    if not os.path.exists(VIZ_PATH):
        generate_matplotlib_visualization()
    if not os.path.exists(VIZ_PATH):
        return jsonify({"success": False, "message": "Visualization not available"}), 404
    return send_file(VIZ_PATH, mimetype="image/png")


@app.route("/api/dataset/kaggle-sync", methods=["POST"])
@login_required(role="admin")
def dataset_kaggle_sync():
    payload = request.get_json(silent=True) or {}
    file_path = payload.get("file_path", "")
    try:
        df = sync_kaggle_dataset(file_path)
        metrics = train_all_models()
        TRAFFIC_CHAIN.add_public_record(
            "dataset_sync",
            {"rows": len(df), "source": "kaggle", "dataset": KAGGLE_DATASET},
        )
        return jsonify(
            {
                "success": True,
                "message": "Kaggle dataset synced to CSV and models retrained",
                "rows": len(df),
                "preview": df.head(5).to_dict(orient="records"),
                "model": {
                    "type": metrics["model_type"],
                    "mse": metrics["mse"],
                    "r2": metrics["r2"],
                    "training_rows": metrics["training_rows"],
                },
            }
        )
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")


@app.route("/")
def serve_index():
    return open(os.path.join(FRONTEND_DIR, "index.html"), encoding="utf-8").read()


@app.route("/<path:filename>")
def serve_frontend(filename):
    allowed = {"style.css", "script.js", "index.html"}
    if filename not in allowed:
        return jsonify({"error": "Not found"}), 404
    file_path = os.path.join(FRONTEND_DIR, filename)
    if not os.path.exists(file_path):
        return jsonify({"error": "Not found"}), 404
    if filename.endswith(".css"):
        return open(file_path, encoding="utf-8").read(), 200, {"Content-Type": "text/css"}
    if filename.endswith(".js"):
        return open(file_path, encoding="utf-8").read(), 200, {"Content-Type": "application/javascript"}
    return open(file_path, encoding="utf-8").read()


@app.route("/auth_config_google.json")
def serve_google_config():
    return open(os.path.join(ROOT_DIR, "auth_config_google.json"), encoding="utf-8").read(), 200, {"Content-Type": "application/json"}


@app.route("/auth_config_github.json")
def serve_github_config():
    return open(os.path.join(ROOT_DIR, "auth_config_github.json"), encoding="utf-8").read(), 200, {"Content-Type": "application/json"}


init_database()
generate_synthetic_dataset(count=1200)
refresh_dataset_metadata()
generate_matplotlib_visualization()
train_model()

conn = get_db()
admin_exists = conn.execute(
    "SELECT COUNT(*) FROM users WHERE role = 'admin'"
).fetchone()[0]
if admin_exists == 0:
    conn.execute(
        """
        INSERT OR IGNORE INTO users (username, email, password_hash, role, created_at)
        VALUES ('sysadmin', 'admin@traffic.dev', ?, 'admin', ?)
        """,
        (hash_password("admin123"), datetime.utcnow().isoformat()),
    )
    conn.commit()
conn.close()

if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    
    print("[*] Hydrating core database with historical data records...")
    total_records = generate_synthetic_dataset(count=1200)
    print(f"[*] Database operational. Total telemetry records: {total_records}")
    
    print("[*] Priming analytical models...")
    train_all_models()
    print(f"[*] Hybrid models initialized (TensorFlow: {TF_AVAILABLE})")
    
    app.run(host="0.0.0.0", port=5000, debug=True)
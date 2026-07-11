"""
Fetch or refresh the traffic volume CSV dataset.
Usage:
  python fetch_kaggle_dataset.py          # try Kaggle, else use bundled seed
  python fetch_kaggle_dataset.py --local  # skip Kaggle, write seed CSV only
"""
import argparse
import os

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CSV_PATH = os.path.join(DATA_DIR, "traffic_volume.csv")
KAGGLE_DATASET = "fedesoriano/traffic-prediction-dataset"

SEED_ROWS = [
    (0, 4748.0107), (1, 4932.7), (2, 4851.064), (3, 5144.389), (4, 352.11523),
    (5, 5825.4106), (6, 275.9654), (7, 5251.7627), (8, 6083.2603), (9, 2885.1006),
    (10, 667.4091), (11, 423.4656), (12, 5500.569), (13, 5664.006), (14, 5753.576),
    (15, 3231.677), (16, 4759.1123), (17, 385.20245), (18, 333.8842), (19, 5719.8394),
    (20, 6432.6553), (21, 4465.168), (22, 4152.8857), (23, 2079.8354), (24, 354.17993),
    (25, 5398.5654), (26, 5231.52), (27, 3361.493), (28, 1862.222), (29, 361.64658),
    (30, 5758.524), (31, 6430.7256), (32, 5432.9917), (33, 4861.1094), (34, 3441.2112),
    (35, 1836.4067), (36, 796.54974), (37, 4649.1953), (38, 2165.244), (39, 1174.9451),
    (40, 449.97882), (41, 912.42975), (42, 3451.78), (43, 2613.7957), (44, 870.9888),
    (45, 321.1347), (46, 1059.0686), (47, 1807.4045), (48, 2730.533), (49, 3763.8787),
    (50, 4308.187), (51, 4308.187), (52, 4312.982), (53, 4312.982), (54, 4404.8936),
    (55, 4489.8447), (56, 618.0069), (57, 837.03503), (58, 5191.866), (59, 6391.954),
    (60, 4422.3037), (61, 3347.1702), (62, 2187.815), (63, 335.26715), (64, 335.59872),
    (65, 879.4678), (66, 5805.36), (67, 3547.5408), (68, 999.423), (69, 394.0456),
    (70, 912.4195), (71, 3005.7737), (72, 6212.463), (73, 4732.882), (74, 380.31592),
    (75, 289.34857), (76, 345.29428), (77, 5220.287), (78, 6400.871), (79, 2246.3713),
    (80, 369.62964), (81, 2569.6624), (82, 5360.6475), (83, 5684.003), (84, 4781.0005),
    (85, 2673.7825), (86, 2673.7825), (87, 2033.3284), (88, 586.26074), (89, 404.81976),
    (90, 1238.8887), (91, 2765.932), (92, 3576.6274), (93, 4407.328), (94, 1383.0282),
    (95, 4038.3716), (96, 1954.7677), (97, 1618.934), (98, 283.57846), (99, 5102.1177),
]


def write_seed_csv():
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(CSV_PATH):
        existing = pd.read_csv(CSV_PATH)
        if len(existing) >= 500:
            print(f"Seed skipped — {CSV_PATH} already has {len(existing)} rows.")
            return existing
    df = pd.DataFrame(SEED_ROWS, columns=["ID", "traffic_volume"])
    if os.path.exists(CSV_PATH):
        df = pd.concat([pd.read_csv(CSV_PATH), df]).drop_duplicates(subset=["ID"]).sort_values("ID")
    df.to_csv(CSV_PATH, index=False)
    print(f"Wrote seed CSV: {CSV_PATH} ({len(df)} rows)")
    return df


def fetch_from_kaggle(file_path=""):
    import kagglehub
    from kagglehub import KaggleDatasetAdapter

    df = kagglehub.load_dataset(
        KaggleDatasetAdapter.PANDAS,
        KAGGLE_DATASET,
        file_path,
    )
    if "ID" not in df.columns:
        df = df.reset_index().rename(columns={"index": "ID"})
    if "traffic_volume" not in df.columns:
        volume_col = [c for c in df.columns if "volume" in c.lower() or "traffic" in c.lower()]
        if volume_col:
            df = df.rename(columns={volume_col[0]: "traffic_volume"})
        else:
            numeric_cols = df.select_dtypes(include="number").columns.tolist()
            df["traffic_volume"] = df[numeric_cols[-1]]
    df = df[["ID", "traffic_volume"]].copy()
    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(CSV_PATH, index=False)
    print(f"Kaggle dataset saved: {CSV_PATH} ({len(df)} rows)")
    print("First 5 records:\n", df.head())
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="Use bundled seed only")
    args = parser.parse_args()
    if args.local:
        write_seed_csv()
        return
    try:
        fetch_from_kaggle()
    except Exception as exc:
        print(f"Kaggle fetch failed ({exc}). Falling back to bundled seed CSV.")
        write_seed_csv()


if __name__ == "__main__":
    main()

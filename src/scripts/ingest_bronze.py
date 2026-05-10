import pandas as pd
import numpy as np
import io 
import boto3
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info("Starting ingestion to bronze layer...")

s3 = boto3.client(
    's3', 
    endpoint_url='http://localhost:4566', 
    aws_access_key_id='test', 
    aws_secret_access_key='test', 
    region_name='us-east-1'
)
bronze_bucket = "dos-boeing-737-max-bronze-layer"

# load data from kaggle dataset (already downloaded and unzipped in local)
path = "/home/rosvend/.cache/kagglehub/datasets/hooong/aviation-maintenance-dataset-from-the-ngafid/versions/1/2days/2days/flight_data.pkl"
data_dict = pd.read_pickle(path)

sensor_cols = [
    'volt1', 'volt2', 'amp1', 'amp2', 'FQtyL', 'FQtyR', 'E1_FFlow', 
    'E1_OilT', 'E1_OilP', 'E1_RPM', 'E1_CHT1', 'E1_CHT2', 'E1_CHT3', 
    'E1_CHT4', 'E1_EGT1', 'E1_EGT2', 'E1_EGT3', 'E1_EGT4', 'OAT', 
    'IAS', 'VSpd', 'NormAc', 'AltMSL'
]

# Extract 50 flights
vuelos_ids = list(data_dict.keys())[:50]
lista_dfs = []

for v_id in vuelos_ids:
    raw_data = data_dict[v_id]
    if raw_data.shape[0] == 23:
        raw_data = raw_data.T
    df_vuelo = pd.DataFrame(raw_data, columns=sensor_cols)
    df_vuelo['flight_id'] = v_id
    lista_dfs.append(df_vuelo)

df_raw = pd.concat(lista_dfs, ignore_index=True)

# 3. Save as Parquet and upload to S3 directly from memory
parquet_buffer = io.BytesIO()
df_raw.to_parquet(parquet_buffer, index=False)
s3.put_object(Bucket=bronze_bucket, Key='raw/flight_data.parquet', Body=parquet_buffer.getvalue())

logger.info(f"Success! Uploaded {len(df_raw)} records to s3://{bronze_bucket}/raw/flight_data.parquet")
sensor_cols = [
    'volt1', 'volt2', 'amp1', 'amp2', 'FQtyL', 'FQtyR', 'E1_FFlow', 
    'E1_OilT', 'E1_OilP', 'E1_RPM', 'E1_CHT1', 'E1_CHT2', 'E1_CHT3', 
    'E1_CHT4', 'E1_EGT1', 'E1_EGT2', 'E1_EGT3', 'E1_EGT4', 'OAT', 
    'IAS', 'VSpd', 'NormAc', 'AltMSL'
]
import pandas as pd
import boto3
import io
from sklearn.preprocessing import MinMaxScaler
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info("Initializing Glue ETL Simulation (Bronze -> Silver)...")

# 1. Connect to LocalStack S3
s3 = boto3.client(
    's3', 
    endpoint_url='http://localhost:4566', 
    aws_access_key_id='test', 
    aws_secret_access_key='test', 
    region_name='us-east-1'
)

bronze_bucket = "dos-boeing-737-max-bronze-layer"
silver_bucket = "dos-boeing-737-max-silver-layer"
raw_key = "raw/flight_data.parquet"
clean_key = "cleaned/flight_data_normalized.parquet"

# 2. Extract: Read Raw Data from Bronze
logger.info(f"Extracting data from s3://{bronze_bucket}/{raw_key}")
response = s3.get_object(Bucket=bronze_bucket, Key=raw_key)
df = pd.read_parquet(io.BytesIO(response['Body'].read()))

# 3. Transform: Data Cleaning
logger.info("Cleaning Data: Removing incomplete sensor logs (NaNs)...")
initial_rows = len(df)
df_clean = df.dropna().copy()
logger.info(f"Dropped {initial_rows - len(df_clean)} faulty records.")

# 4. Transform: Normalization
logger.info("Normalizing Data: Applying Min-Max Scaling to sensor telemetry...")
scaler = MinMaxScaler()
sensor_columns = [col for col in df_clean.columns if col != 'flight_id']

# Scale all sensor data between 0 and 1 
df_clean[sensor_columns] = scaler.fit_transform(df_clean[sensor_columns])

# 5. Load: Write Clean Data to Silver
logger.info(f"Loading data to s3://{silver_bucket}/{clean_key}")
out_buffer = io.BytesIO()
df_clean.to_parquet(out_buffer, index=False)
s3.put_object(Bucket=silver_bucket, Key=clean_key, Body=out_buffer.getvalue())

logger.info("ETL Job Completed Successfully!")
logger.info(df_clean[['flight_id', 'E1_RPM', 'E1_OilT', 'IAS']].head())
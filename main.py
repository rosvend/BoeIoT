import kagglehub
import pandas as pd

path = kagglehub.dataset_download("hooong/aviation-maintenance-dataset-from-the-ngafid")
df = pd.read_csv(path)
print(df.head())
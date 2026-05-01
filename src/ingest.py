import pandas as pd
import numpy as np

sensor_cols = [
    'volt1', 'volt2', 'amp1', 'amp2', 'FQtyL', 'FQtyR', 'E1_FFlow', 
    'E1_OilT', 'E1_OilP', 'E1_RPM', 'E1_CHT1', 'E1_CHT2', 'E1_CHT3', 
    'E1_CHT4', 'E1_EGT1', 'E1_EGT2', 'E1_EGT3', 'E1_EGT4', 'OAT', 
    'IAS', 'VSpd', 'NormAc', 'AltMSL'
]

#load pickle file
path = "/home/rosvend/.cache/kagglehub/datasets/hooong/aviation-maintenance-dataset-from-the-ngafid/versions/1/2days/2days/flight_data.pkl"
data_dict = pd.read_pickle(path)

vuelos_ids = list(data_dict.keys())[:50]
lista_dfs = []

for v_id in vuelos_ids:
    raw_data = data_dict[v_id]
    
    if raw_data.shape[0] == 23:
        raw_data = raw_data.T
    
    df_vuelo = pd.DataFrame(raw_data, columns=sensor_cols)
    df_vuelo['flight_id'] = v_id
    lista_dfs.append(df_vuelo)

df_muestra = pd.concat(lista_dfs, ignore_index=True)
print(f"Dataset listo para EDA. Registros: {df_muestra.shape[0]}")
print(df_muestra[['flight_id', 'E1_RPM', 'AltMSL', 'IAS']].head())
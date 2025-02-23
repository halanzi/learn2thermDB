"""Ingest Uniprot

Download raw uniprot data files, not tracked by dvc.
Only creates a metadata file for the purposes of dvc.

Due to this circumventing of DVC, the data files from uniprot will have to be deleted
abd the stage force ran for it to retrieve new data.
"""
import pandas as pd
from yaml import dump as yaml_dump
from yaml import safe_load as yaml_load

import learn2therm.utils

import datetime
from ftplib import FTP
from tqdm import tqdm
import logging
import os

try:
    EMAIL = os.environ['ENV_EMAIL']
except KeyError:
    raise KeyError('Must set environmental variables `ENV_EMAIL`')
if 'LOGLEVEL' in os.environ:
    LOGLEVEL = os.environ['LOGLEVEL']
    LOGLEVEL = getattr(logging, LOGLEVEL)
else:
    LOGLEVEL = logging.INFO
LOGNAME = __file__
LOGFILE = f'./logs/{os.path.basename(__file__)}.log'
# get the logger in subprocesses
logger = learn2therm.utils.start_logger_if_necessary(LOGNAME, LOGFILE, LOGLEVEL, filemode='w')

# set up ftp
FTP_ADDRESS = 'ftp.uniprot.org'
FTP_DIR = '/pub/databases/uniprot/current_release/knowledgebase/taxonomic_divisions/'

def ftp_get_file_progress_bar(filename, endpoint_dir):
    ftp = FTP(FTP_ADDRESS)
    ftp.login(user="anonymous", passwd=EMAIL)
    ftp.cwd(FTP_DIR) 

    file_size = ftp.size(filename)
    with open(endpoint_dir+f'{filename}', 'wb') as file:
        pbar = tqdm(range(file_size))
        def write_file(data):
            file.write(data)
            pbar.n += len(data)
            pbar.refresh()

        ftp.retrbinary(f"RETR {filename}", write_file, blocksize=262144)
    ftp.close()

if __name__ == "__main__":
    # DVC tracked parameters
    with open("./params.yaml", "r") as stream:
        params = yaml_load(stream)['get_raw_data_proteins']
    logger.info(f"Loaded parameters: {params}")

    if not os.path.exists('./data/uniprot'):
        os.mkdir('./data/uniprot')

    # download the raw data into temporary files
    dir = './data/uniprot'
    addresses = [
        'uniprot_sprot_archaea.xml.gz',
        'uniprot_sprot_bacteria.xml.gz',
        'uniprot_trembl_archaea.xml.gz',
        'uniprot_trembl_bacteria.xml.gz',
    ]
    if params['dev_only_one_uniprot_file']:
        addresses = addresses[:1]
        logger.info(f"Downloading only {addresses}")

    # download each file from uniprot
    for i, address in enumerate(addresses):
        if address in os.listdir(dir):
            logger.info(f"Address exists, skipping: {address}")
            continue
        ftp_get_file_progress_bar(address, endpoint_dir='./data/uniprot/')
        logger.info(f"Completed download of {address}")

    # save metrics
    date_pulled = str(datetime.datetime.now().strftime("%m/%d/%Y"))
    with open('./data/uniprot/uniprot_pulled_timestamp', 'w') as file:
        file.write(date_pulled)
                      
    metrics = {}
    metrics['protein_pulled_date'] = date_pulled
    with open('./data/metrics/s0.1_metrics.yaml', "w") as stream:
        yaml_dump(metrics, stream)




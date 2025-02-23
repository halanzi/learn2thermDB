"""Ingest raw PFAM HMMs


Sources
-------
[1] R. D. Finn et al., “The Pfam protein families database: towards a more sustainable future,” 
Nucleic Acids Research, vol. 44, no. D1, pp. D279–D285, Jan. 2016, doi: 10.1093/nar/gkv1344.

[2] T. Paysan-Lafosse et al., “InterPro in 2022,” 
Nucleic Acids Research, vol. 51, no. D1, pp. D418–D427, Jan. 2023, doi: 10.1093/nar/gkac993.


Notes
-----
TODO:
- delete gz file after unzipping
"""
# system dependecies
import datetime
from ftplib import FTP
import gzip
import logging
import os
import shutil
import tarfile
from tqdm import tqdm

# library dependecies
from yaml import dump as yaml_dump
from yaml import safe_load as yaml_load

# local dependencies
import learn2therm.database
import learn2therm.utils

## get environmental variables
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
FTP_ADDRESS = 'ftp.ebi.ac.uk'
FTP_DIR = '/pub/databases/Pfam/current_release/'

def download_ftp_file(server, remote_file, local_file):
    ftp = FTP(server)
    ftp.login(user="anonymous", passwd='')
    ftp.cwd('/')  # Set the current directory (if needed)

    with open(local_file, 'wb') as file:
        ftp.retrbinary('RETR ' + remote_file, file.write)

    ftp.quit()

if __name__ == "__main__":
    try:
        os.makedirs('./data/HMM', exist_ok=True)
    except OSError as e:
        logger.error(f'Error creating directory: {e}')

    # make the output directory
    os.makedirs('./data/validation/hmmer', exist_ok=True)

    # download the raw data into temporary files 
    local_dir = './data/validation/hmmer/Pfam-A.hmm.gz'
    remote_file = '/pub/databases/Pfam/current_release/Pfam-A.hmm.gz'
    
    download_ftp_file(FTP_ADDRESS, remote_file, local_dir)
    logger.info("download from FTP")

    # unzip the files
    extracted_file = './data/validation/hmmer/Pfam-A.hmm'

    with gzip.open(local_dir, 'rb') as f_in:
        with open(extracted_file, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)

    logger.info(f'Extracted the file can be found in: {extracted_file}')

    # save metrics
    date_pulled = str(datetime.datetime.now().strftime("%m/%d/%Y"))
                      
    metrics = {}
    metrics['HMM_pulled_date'] = str(date_pulled)
    with open('./data/validation/hmmer/s2.5_metrics.yaml', "w") as stream:
        yaml_dump(metrics, stream)

"""Extract number of possible protein pairs among chosen taxa pairs.

Load the taxa information and the number of proteins for each taxa. Compute
The toral search space for these taxa, then select random pairs
to run a resource test on.

Inputs
------
- data/taxa_pairs/pair_labels : contains boolean labeling of taxa pairs
- data/taxa_pairs/alignment : contains alignment scores for taxa pairs and their taxids
- data/proteins

Outputs
-------
- data/metrics/t1.2_filtered_search_space.yaml : expected results and resources required to
    run BLASTP on all taxa pairs based ona  resource test.
"""
from yaml import safe_load as yaml_load
from yaml import dump as yaml_dump
import os
import shutil
import time
import tempfile
# set the dask config
os.environ['DASK_CONFIG'] = './.config/dask/'

import pandas as pd
import duckdb as ddb
import logging

from codecarbon import OfflineEmissionsTracker
import dask
import dask_jobqueue
import distributed

import learn2therm.utils
import learn2therm.blast

if 'LOGLEVEL' in os.environ:
    LOGLEVEL = os.environ['LOGLEVEL']
    LOGLEVEL = getattr(logging, LOGLEVEL)
else:
    LOGLEVEL = logging.INFO
LOGNAME = __file__
LOGFILE = f'./logs/{os.path.basename(__file__)}.log'

NUM_SAMPLE = 100

def prep_taxa_protein_db():
    tmpdir = tempfile.mkdtemp(dir='./tmp', )
    conn = ddb.connect(tmpdir+'/proteins.db', read_only=False)
    conn.execute("CREATE TABLE proteins AS SELECT taxid AS taxid, pid AS pid, protein_seq AS sequence FROM read_parquet('./data/proteins/*.parquet')")
    conn.commit()

    # now create table of taxa pairs
    conn.execute("CREATE TEMP TABLE pair_labels AS SELECT * FROM read_parquet('./data/taxa_pairs/pair_labels/*.parquet')")
    conn.execute("CREATE TEMP TABLE pair_scores AS SELECT * FROM read_parquet('./data/taxa_pairs/alignment/*.parquet')")
    conn.execute("CREATE TABLE pairs AS SELECT * FROM pair_labels INNER JOIN pair_scores ON (pair_labels.__index_level_0__ = pair_scores.__index_level_0__) WHERE pair_labels.is_pair=True")
    conn.commit()
    conn.execute("CREATE INDEX meso_index ON pairs (subject_id)")
    conn.execute("CREATE INDEX thermo_index ON pairs (query_id)")
    conn.commit()
    conn.close()

    return tmpdir, tmpdir+'/proteins.db'

def worker_function(taxa_alignment_worker, wakeup=None):
    """Run one taxa pair on a worker."""
    # we want to wait for execution to see if this worker is actually being used
    # or if it is in the process of being killed
    if wakeup is not None:
        time.sleep(wakeup)
    # begin execution
    t0=time.time()
    pair_indexes = str(taxa_alignment_worker.pair_indexes[0]) + '-' + str(taxa_alignment_worker.pair_indexes[1])
    worker_logfile = f'./logs/t1.2_workers/pair_{pair_indexes}.log'
    logger = learn2therm.utils.start_logger_if_necessary(LOGNAME, worker_logfile, LOGLEVEL, filemode='a', worker=True)
    learn2therm.blast.logger = learn2therm.utils.start_logger_if_necessary('learn2therm.blast', worker_logfile, LOGLEVEL, filemode='a', worker=True)
    learn2therm.io.logger = learn2therm.utils.start_logger_if_necessary('learn2therm.io', worker_logfile, LOGLEVEL, filemode='a', worker=True)

    logger.info(f"recieved pair {taxa_alignment_worker.pair_indexes}")
    
    out_dic = taxa_alignment_worker.run()
    t1=time.time()
    logger.info(f"Completed pair {taxa_alignment_worker.pair_indexes}. Took {(t1-t0)/60}m")
    return out_dic

def main():
    logger = learn2therm.utils.start_logger_if_necessary(LOGNAME, LOGFILE, LOGLEVEL, filemode='w')
    # load params
    with open("./params.yaml", "r") as stream:
        params = yaml_load(stream)['get_protein_blast_scores']
        logger = learn2therm.utils.start_logger_if_necessary("", LOGFILE, LOGLEVEL, filemode='w')
        shutil.rmtree('./logs/t1.2_workers/', ignore_errors=True, onerror=None)
        os.mkdir('./logs/t1.2_workers/')
    logger.info(f"Loaded parameters: {params}")

    # setup the database and get some pairs to run
    tmpdir_database, db_path = prep_taxa_protein_db()
    tmpdir_output = tempfile.mkdtemp(dir='./tmp', )
    logger.info(f"Temporary directory of output: {tmpdir_output}, path to database {db_path}")
    conn = ddb.connect(db_path, read_only=True)
    pairs = conn.execute("SELECT query_id, subject_id FROM pairs ORDER BY RANDOM()").fetchall()
    logger.info(f"Total number of taxa pairs: {len(pairs)} in pipeline")

    # get the number of possible proteins for each taxa pair
    # if it is 0 ignore pair
    # first get a mapping of protein counts
    max_protein_length = params['max_protein_length']
    protein_counts = conn.execute(f"SELECT taxid, COUNT(*) FROM proteins WHERE LENGTH(sequence)<={max_protein_length} GROUP BY taxid").df()
    protein_counts = protein_counts[protein_counts['count_star()'] > 0]
    protein_counts = protein_counts.set_index('taxid')['count_star()']
    logger.debug(f'Valid taxa with proteins: {list(protein_counts.index)}')
    total_space = 0
    new_pairs = []
    for thermo_index, meso_index in pairs:
        thermo_index = int(thermo_index)
        meso_index = int(meso_index)
        try:
            thermo_count = protein_counts[thermo_index]
            meso_count = protein_counts[meso_index]
        except KeyError:
            logger.debug(f"Pair {thermo_index}, {meso_index} has no possible proteins to search. Ignoring.")
            continue

        total_space += thermo_count*meso_count
        new_pairs.append((thermo_index, meso_index))
    pairs = new_pairs
    logger.info(f"Total number of taxa pairs with possible proteins: {len(pairs)}")
    logger.info(f"Total possible protein search space among selected taxa pairs: {total_space}")

    # sample the number of pairs to actually run
    pairs = pairs[:NUM_SAMPLE]
    logger.info(f"Sampling {NUM_SAMPLE} taxa pairs to run resource test")

    # get aligner handler class
    alignment_method = params['method']
    try:
        aligner_params = params[f"method_{alignment_method}_params"]
    except:
        raise ValueError(f"specified method {alignment_method} for alignment but found no parameters for it.")
    
    if alignment_method == 'blast':
        Aligner = getattr(learn2therm.blast, 'BlastAlignmentHandler')
    elif alignment_method == 'diamond':
        Aligner = getattr(learn2therm.blast, 'DiamondAlignmentHandler')
    else:
        raise ValueError(f"Unknown alignment method {alignment_method}")

    # start a cluster object and run it
    t0 = time.time()
    Cluster = getattr(dask_jobqueue, params['dask_cluster_class'])
    cluster = Cluster(silence_logs=None)

    # determine if we have the job capacity to be killing workers after jobs
    # heuristically at three, the few is more likely that all jobs die
    # this option is to save compute when a task would normally start on a worker that just
    # finished a job
    if params['n_jobs'] > 3:
        minimum_jobs = params['n_jobs'] - 1
    else:
        minimum_jobs = 1
    cluster.adapt(minimum=minimum_jobs, maximum=params['n_jobs'], target_duration='5s')

    logger.info(f"{cluster.job_script()}")

    # create plugin to use for worker killing and start the client
    with distributed.Client(cluster) as client:
        # run one without killer workers, faster option for 
        # fast tasks
        logger.info(f"Running primary fast sweep")
        with learn2therm.blast.TaxaAlignmentClusterState(
            pairs=pairs,
            client=client,
            worker_function=lambda a: worker_function(a, None),
            max_seq_len=params['max_protein_length'],
            protein_database=db_path,
            output_dir=tmpdir_output,
            aligner_class=Aligner,
            metrics=params['blast_metrics'],
            alignment_params=aligner_params,
            restart=True,
            killer_workers=False
        ) as futures:
            for i, future in enumerate(futures):
                pass
    
    t1 = time.time()
    total_time = (t1-t0)/60/60
    logger.info(f'Completed all tasks, took {total_time} hr')

    # compute metrics
    results = pd.read_csv(tmpdir_output+'/completion_state.metadat')
    logger.info(f"{results['hits'].isna().sum()} taxa pairs failed to complete")
    results = results[~results['hits'].isna()]
    emissions = float(results['emissions'].sum())
    hits = int(results['hits'].sum())
    pairwise_space = int(results['pw_space'].sum())


    # extrapolate out metrics to total space
    scaling_factor = total_space/pairwise_space
    expected_hits = int(hits*scaling_factor)
    expected_emissions = float(emissions*scaling_factor)
    expected_time = float(total_time*scaling_factor)

    metrics = {}
    metrics['expected_protein_align_emissions'] = expected_emissions
    metrics['expected_protein_align_hits'] = expected_hits
    metrics['expected_protein_align_time'] = expected_time
    metrics['expected_protein_aling_return'] = hits/pairwise_space

    with open('./data/metrics/t1.2_metrics.yaml', "w") as stream:
        yaml_dump(metrics, stream)

if __name__ == '__main__':
    main()

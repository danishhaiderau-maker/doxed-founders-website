from research.genome.loader import load_table, default_db_path

def load_execution_genomes(db_path=None):
    return load_table(db_path or default_db_path(), "execution_genome")

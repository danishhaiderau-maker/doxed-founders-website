from research.genome.loader import load_table, default_db_path

def load_market_genomes(db_path=None):
    return load_table(db_path or default_db_path(), "market_genome")

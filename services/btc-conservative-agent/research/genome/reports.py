"""Genome report writer."""
from research.genome.run_analyzer import run_genome_analyzer

def generate_reports(db_path=None):
    return run_genome_analyzer(db_path=db_path)

"""Genome dashboard payload builder."""
from research.genome.run_analyzer import run_genome_analyzer

def build_dashboard_payload(db_path=None):
    return run_genome_analyzer(db_path=db_path)

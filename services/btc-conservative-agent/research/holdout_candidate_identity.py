"""Candidate identity checks for already verified holdout provenance."""
import re


def candidate_identity_matches(original, outcome, proof, *, policy, signature, seal_request_id):
    if proof.get('schema') != 'verified_counterfactual_collection_provenance_v1':
        # Preserve the existing paper identity contract. This helper does not
        # replace the producer's bundle verification.
        return (not outcome.get('counterfactual_identity') and
                outcome.get('source_lifecycle_identity', {}).get('policy_signature') == signature)
    expected = {
        'epoch_id': original.get('dataset_epoch'),
        'source_episode_id': original.get('source_episode_id'),
        'opportunity_id': original.get('opportunity_id'),
        'policy_id': policy, 'policy_signature': signature,
        'direction': original.get('direction'), 'seal_request_id': seal_request_id,
    }
    return (
        all(isinstance(value, str) and value for value in expected.values())
        and expected['direction'] in {'LONG', 'SHORT'}
        and not outcome.get('source_lifecycle_identity')
        and outcome.get('counterfactual_identity') == expected
        and proof.get('counterfactual_identity') == expected
        and proof.get('entry_semantic_replay_verified') is True
        and proof.get('terminal_semantic_replay_verified') is True
        and isinstance(proof.get('replay_proof_sha256'), str)
        and re.fullmatch('[0-9a-f]{64}', proof['replay_proof_sha256']) is not None
        and outcome.get('replay_proof_sha256') == proof['replay_proof_sha256']
    )

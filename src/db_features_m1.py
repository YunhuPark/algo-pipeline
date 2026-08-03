from src.db_factory import get_connection
import sqlite3
import hashlib
from datetime import datetime
import json
import uuid
import os

from src.db_tracking import resolve_tracking_db_path
from src.db import resolve_algo_db_path

def calculate_edit_distance(orig: str, modified: str) -> int:
    return abs(len(orig) - len(modified))

def create_revision(run_id: str, content_id: str, content_payload: str, actor_type: str = "ai", parent_revision_id: str = None, edit_reason: str = None, prompt_version: str = None, policy_version: str = None, model_provider: str = None, model_id: str = None):
    conn = get_connection(str(resolve_tracking_db_path()))
    cursor = conn.cursor()
    
    revision_id = str(uuid.uuid4())
    content_hash = hashlib.sha256(content_payload.encode()).hexdigest()
    
    if parent_revision_id is None:
        revision_number = 0
        revision_type = "creation"
        edit_dist = 0
    else:
        cursor.execute("SELECT revision_number, content_payload FROM content_revisions WHERE revision_id=?", (parent_revision_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError("Parent revision not found")
        revision_number = row[0] + 1
        revision_type = "edit"
        edit_dist = calculate_edit_distance(row[1], content_payload)
        
    cursor.execute("""
        INSERT INTO content_revisions (revision_id, content_id, run_id, parent_revision_id, revision_number, content_payload, content_hash, revision_type, actor_type, edit_reason, edit_distance, prompt_version, policy_version, model_provider, model_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (revision_id, content_id, run_id, parent_revision_id, revision_number, content_payload, content_hash, revision_type, actor_type, edit_reason, edit_dist, prompt_version, policy_version, model_provider, model_id))
    
    conn.commit()
    conn.close()
    return revision_id

# === Analytics Import Service ===
def import_performance_snapshot(content_id: str, data_source: str, reach: int, saves: int, shares: int, captured_at: str = None):
    if captured_at is None:
        captured_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
    conn = get_connection(str(resolve_algo_db_path()))
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT OR IGNORE INTO performance_snapshots (content_id, data_source, captured_at, reach, saves, shares)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (content_id, data_source, captured_at, reach, saves, shares))
    
    conn.commit()
    conn.close()

# === Experiment Assignment Service ===
def assign_variant(experiment_id: str, assignment_unit_id: str, allocation_salt: str, variants: list) -> str:
    hash_input = f"{experiment_id}_{assignment_unit_id}_{allocation_salt}".encode()
    hash_val = int(hashlib.sha256(hash_input).hexdigest(), 16)
    assigned_variant = variants[hash_val % len(variants)]
    
    conn = get_connection(str(resolve_tracking_db_path()))
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT OR IGNORE INTO experiment_assignments (assignment_id, experiment_id, publication_opportunity_id, variant_id, allocation_salt, assignment_version)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (str(uuid.uuid4()), experiment_id, assignment_unit_id, assigned_variant, allocation_salt, '1.0'))
    
    # Read back to ensure we get the assigned one even if it was IGNOREd
    cursor.execute("SELECT variant_id FROM experiment_assignments WHERE experiment_id=? AND publication_opportunity_id=?", (experiment_id, assignment_unit_id))
    ret = cursor.fetchone()[0]
    
    conn.commit()
    conn.close()
    return ret

# === Benchmark Import Service ===
def import_benchmark(account_id: str, post_id: str, topic: str, hook_type: str, views: int):
    conn = get_connection(str(resolve_algo_db_path()))
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO competitor_benchmarks (account_id, post_id, topic, hook_type, views)
        VALUES (?, ?, ?, ?, ?)
    """, (account_id, post_id, topic, hook_type, views))
    conn.commit()
    conn.close()

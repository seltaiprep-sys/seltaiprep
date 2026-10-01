"""File-based storage for reading tests with security, logging, and cleanup."""

import json
import os
import re
import uuid
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

# Use absolute path for safety
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
TEST_STORAGE_DIR = os.path.join(BASE_DIR, 'instance', 'tests')
os.makedirs(TEST_STORAGE_DIR, exist_ok=True)

# File extension
FILE_EXT = '.json'

# Default TTL for tests (days)
DEFAULT_TTL_DAYS = 30


def _safe_filename(test_id: str) -> str:
    """
    Sanitize test_id to prevent path traversal attacks.
    Only allow alphanumeric, underscore, and hyphen.
    """
    if not test_id:
        test_id = str(uuid.uuid4())[:8]
    safe_id = re.sub(r'[^a-zA-Z0-9_-]', '', test_id)
    if not safe_id:
        safe_id = str(uuid.uuid4())[:8]
    return safe_id


def _get_filepath(test_id: str) -> str:
    """Get absolute filepath for a test ID."""
    safe_id = _safe_filename(test_id)
    return os.path.join(TEST_STORAGE_DIR, f'test_{safe_id}{FILE_EXT}')


def save_test(test_data: Dict, test_id: Optional[str] = None) -> str:
    """
    Save test data to JSON file with full content.
    
    Args:
        test_data: Complete test data (passages, questions, answers, etc.)
        test_id: Optional test ID (generated if not provided)
    
    Returns:
        test_id: str
    """
    try:
        if not test_id:
            test_id = test_data.get('id') or str(uuid.uuid4())[:8]
        
        safe_id = _safe_filename(test_id)
        filepath = _get_filepath(safe_id)
        
        # Prepare data with metadata
        data = {
            'id': safe_id,
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat(),
            'test_data': test_data, # Full test data
            'correct_answers': test_data.get('correct_answers', {}),
            'question_types': test_data.get('question_types', {}),
            'total_questions': test_data.get('total_questions', 0),
            'difficulty': test_data.get('difficulty', 'medium'),
        }
        
        # Write atomically: write to temp file then rename
        temp_file = filepath + '.tmp'
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        # Atomic rename (works on Unix, and on Windows if file not open)
        if os.path.exists(filepath):
            os.remove(filepath)
        os.rename(temp_file, filepath)
        
        logger.info(f"Test {safe_id} saved successfully to {filepath}")
        return safe_id
        
    except Exception as e:
        logger.error(f"Failed to save test {test_id}: {e}")
        raise RuntimeError(f"Failed to save test: {e}")


def load_test(test_id: str) -> Optional[Dict]:
    """
    Load test data from JSON file.
    
    Args:
        test_id: Test identifier
    
    Returns:
        Test data dict or None if not found
    """
    if not test_id:
        return None
    
    try:
        safe_id = _safe_filename(test_id)
        filepath = _get_filepath(safe_id)
        
        if not os.path.exists(filepath):
            logger.warning(f"Test file not found: {filepath}")
            return None
        
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # If stored with 'test_data' key, return that; otherwise return full data
        if 'test_data' in data and isinstance(data['test_data'], dict):
            # Merge metadata for convenience
            result = data['test_data']
            result['_metadata'] = {
                'id': data.get('id'),
                'created_at': data.get('created_at'),
                'updated_at': data.get('updated_at'),
                'stored_correct_answers': data.get('correct_answers', {}),
                'stored_question_types': data.get('question_types', {}),
            }
            return result
        
        return data
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in test file {test_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to load test {test_id}: {e}")
        return None


def delete_test(test_id: str) -> bool:
    """
    Delete a test file.
    
    Args:
        test_id: Test identifier
    
    Returns:
        True if deleted, False if not found or error
    """
    if not test_id:
        return False
    
    try:
        safe_id = _safe_filename(test_id)
        filepath = _get_filepath(safe_id)
        
        if not os.path.exists(filepath):
            logger.warning(f"Test file not found for deletion: {filepath}")
            return False
        
        os.remove(filepath)
        logger.info(f"Test {safe_id} deleted successfully")
        return True
        
    except Exception as e:
        logger.error(f"Failed to delete test {test_id}: {e}")
        return False


def list_tests(limit: int = 100, include_metadata: bool = True) -> List[Dict]:
    """
    List all available test files with metadata.
    
    Args:
        limit: Maximum number of tests to return
        include_metadata: Include full metadata in each entry
    
    Returns:
        List of test summary dicts
    """
    tests = []
    
    try:
        files = [f for f in os.listdir(TEST_STORAGE_DIR) 
                if f.startswith('test_') and f.endswith(FILE_EXT)]
        files.sort(key=lambda f: os.path.getmtime(os.path.join(TEST_STORAGE_DIR, f)), reverse=True)
        
        for filename in files[:limit]:
            filepath = os.path.join(TEST_STORAGE_DIR, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                test_id = filename.replace('test_', '').replace(FILE_EXT, '')
                entry = {
                    'id': test_id,
                    'filename': filename,
                    'created_at': data.get('created_at'),
                    'updated_at': data.get('updated_at'),
                    'total_questions': data.get('total_questions', 0),
                    'difficulty': data.get('difficulty', 'medium'),
                    'file_size': os.path.getsize(filepath),
                }
                
                if include_metadata:
                    entry['correct_answers'] = data.get('correct_answers', {})
                    entry['question_types'] = data.get('question_types', {})
                
                tests.append(entry)
                
            except Exception as e:
                logger.warning(f"Could not read test file {filename}: {e}")
                continue
        
        logger.info(f"Listed {len(tests)} tests")
        return tests
        
    except Exception as e:
        logger.error(f"Failed to list tests: {e}")
        return []


def cleanup_old_tests(days: int = DEFAULT_TTL_DAYS) -> int:
    """
    Delete tests older than specified days.
    
    Args:
        days: Age threshold in days
    
    Returns:
        Number of deleted tests
    """
    cutoff = datetime.now() - timedelta(days=days)
    deleted_count = 0
    
    try:
        files = [f for f in os.listdir(TEST_STORAGE_DIR) 
                if f.startswith('test_') and f.endswith(FILE_EXT)]
        
        for filename in files:
            filepath = os.path.join(TEST_STORAGE_DIR, filename)
            try:
                # Get creation/modification time from file system
                mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
                if mtime < cutoff:
                    os.remove(filepath)
                    deleted_count += 1
                    logger.info(f"Deleted old test: {filename}")
            except Exception as e:
                logger.warning(f"Could not delete {filename}: {e}")
        
        logger.info(f"Cleanup completed: {deleted_count} tests deleted (older than {days} days)")
        return deleted_count
        
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        return deleted_count


def get_test_stats() -> Dict:
    """
    Get statistics about stored tests.
    
    Returns:
        Dict with total_count, total_size, oldest, newest
    """
    stats = {
        'total_count': 0,
        'total_size_bytes': 0,
        'oldest': None,
        'newest': None,
        'difficulty_counts': {},
    }
    
    try:
        files = [f for f in os.listdir(TEST_STORAGE_DIR) 
                if f.startswith('test_') and f.endswith(FILE_EXT)]
        
        if not files:
            return stats
        
        timestamps = []
        difficulties = {}
        
        for filename in files:
            filepath = os.path.join(TEST_STORAGE_DIR, filename)
            try:
                size = os.path.getsize(filepath)
                mtime = os.path.getmtime(filepath)
                timestamps.append(mtime)
                stats['total_size_bytes'] += size
                stats['total_count'] += 1
                
                # Read difficulty from file
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    diff = data.get('difficulty', 'unknown')
                    difficulties[diff] = difficulties.get(diff, 0) + 1
            except:
                continue
        
        if timestamps:
            stats['oldest'] = datetime.fromtimestamp(min(timestamps)).isoformat()
            stats['newest'] = datetime.fromtimestamp(max(timestamps)).isoformat()
        
        stats['difficulty_counts'] = difficulties
        stats['total_size_mb'] = round(stats['total_size_bytes'] / (1024 * 1024), 2)
        
        return stats
        
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        return stats


# Backward compatibility aliases
save_test_data = save_test
load_test_data = load_test


# Allow running as script for maintenance
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("=" * 60)
    print("TEST STORAGE UTILITY")
    print("=" * 60)
    
    # Demo: save a test
    sample_test = {
        'id': 'demo_001',
        'title': 'Sample Reading Test',
        'total_questions': 40,
        'difficulty': 'medium',
        'correct_answers': {'1': 'A', '2': 'TRUE', '3': 'NOT GIVEN'},
        'question_types': {'1': 'multiple_choice', '2': 'true_false_not_given'},
        'passages': [{'content': 'Sample passage...'}]
    }
    
    test_id = save_test(sample_test)
    print(f" Saved test: {test_id}")
    
    # Load it back
    loaded = load_test(test_id)
    if loaded:
        print(f" Loaded test: {loaded.get('title', 'Untitled')}")
    
    # List tests
    tests = list_tests(limit=5)
    print(f" Found {len(tests)} tests")
    for t in tests:
        print(f" - {t['id']} ({t['created_at']})")
    
    # Stats
    stats = get_test_stats()
    print(f" Stats: {stats['total_count']} tests, {stats['total_size_mb']} MB")
    
    # Cleanup (dry run)
    print(f" Cleanup would delete tests older than {DEFAULT_TTL_DAYS} days")
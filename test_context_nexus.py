#!/usr/bin/env python3
"""
Test script for Context Nexus and Stream of Consciousness features.

Tests:
1. Project HUD functionality
2. Priority scoring with Context Nexus (immortal memories, task linking, mandates)
3. Stream of Consciousness (Idea type and decay)
4. Idea consolidation check
"""

import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent))

from task_priority import Entry, WeightConfig, priority_score, importance_score
from swarm_daemon import fetch_hud_data, check_idea_consolidation


def test_hud_fetch():
    """Test that HUD can be fetched and contains expected sections"""
    print("Testing HUD fetch...")
    hud = fetch_hud_data()
    
    assert "PROJECT HUD" in hud, "HUD should contain PROJECT HUD header"
    assert "OPEN TASKS" in hud, "HUD should contain OPEN TASKS section"
    assert "ACTIVE MANDATES" in hud, "HUD should contain ACTIVE MANDATES section"
    assert "SOURCE OF TRUTH" in hud, "HUD should indicate Source of Truth"
    
    print("✓ HUD fetch test passed\n")


def test_importance_immortal():
    """Test that Critical/High importance entries are marked immortal"""
    print("Testing immortal memory flag...")
    
    # Critical should be immortal
    score, tau, immortal = importance_score("Critical")
    assert immortal == True, "Critical importance should be immortal"
    assert score == 1.0, "Critical importance should have score 1.0"
    
    # High should be immortal
    score, tau, immortal = importance_score("H")
    assert immortal == True, "High importance should be immortal"
    
    # Medium should NOT be immortal
    score, tau, immortal = importance_score("M")
    assert immortal == False, "Medium importance should NOT be immortal"
    
    # None should NOT be immortal
    score, tau, immortal = importance_score(None)
    assert immortal == False, "No importance should NOT be immortal"
    
    print("✓ Immortal memory test passed\n")


def test_immortal_recency():
    """Test that immortal entries bypass recency decay"""
    print("Testing immortal recency bypass...")
    
    weights = WeightConfig()
    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(days=30)).isoformat()  # 30 days old
    
    # Critical entry should have recency=1.0 regardless of age
    critical = Entry(
        id=1, text='Critical rule', timestamp=old_ts, topic='mandate',
        importance='Critical', due=None, links=None, anchor_type='d',
        anchor_choice=None, project_id=None, scope='shared', chat_id=None
    )
    result = priority_score(critical, [], weights, now)
    
    assert result['components']['recency'] == 1.0, f"Critical entry recency should be 1.0, got {result['components']['recency']}"
    assert result['is_immortal'] == True, "Critical entry should be marked immortal"
    
    # Normal entry should decay
    normal = Entry(
        id=2, text='Normal note', timestamp=old_ts, topic='general',
        importance=None, due=None, links=None, anchor_type='n',
        anchor_choice=None, project_id=None, scope='shared', chat_id=None
    )
    result = priority_score(normal, [], weights, now)
    
    assert result['components']['recency'] < 0.5, f"Normal 30-day old entry should have decayed recency, got {result['components']['recency']}"
    assert result['is_immortal'] == False, "Normal entry should NOT be immortal"
    
    print("✓ Immortal recency bypass test passed\n")


def test_task_linking_multiplier():
    """Test that task-linked entries get boosted score"""
    print("Testing task linking multiplier...")
    
    weights = WeightConfig()
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(days=5)).isoformat()
    
    # Entry linked to active task
    linked = Entry(
        id=1, text='Task-related work', timestamp=ts, topic='work',
        importance=None, due=None, links=None, anchor_type='a',
        anchor_choice=None, project_id=None, scope='shared', chat_id=None,
        task_id='task-001'
    )
    
    # Score without active task
    result_no_link = priority_score(linked, [], weights, now, active_task_id=None)
    
    # Score with matching active task
    result_linked = priority_score(linked, [], weights, now, active_task_id='task-001')
    
    assert result_linked['multipliers']['task_link'] == 2.0, f"Task link multiplier should be 2.0, got {result_linked['multipliers']['task_link']}"
    assert result_no_link['multipliers']['task_link'] == 1.0, "No active task should have multiplier 1.0"
    assert result_linked['score'] > result_no_link['score'], "Linked entry should have higher score"
    
    print("✓ Task linking multiplier test passed\n")


def test_mandate_multiplier():
    """Test that decisions get mandate multiplier"""
    print("Testing mandate multiplier...")
    
    weights = WeightConfig()
    now = datetime.now(timezone.utc)
    ts = now.isoformat()
    
    # Decision type
    decision = Entry(
        id=1, text='Important decision', timestamp=ts, topic='arch',
        importance=None, due=None, links=None, anchor_type='d',
        anchor_choice='option-A', project_id=None, scope='shared', chat_id=None
    )
    result = priority_score(decision, [], weights, now)
    
    assert result['multipliers']['mandate'] == 1.5, f"Decision should have mandate multiplier 1.5, got {result['multipliers']['mandate']}"
    
    # Note type (should not have mandate multiplier)
    note = Entry(
        id=2, text='Just a note', timestamp=ts, topic='misc',
        importance=None, due=None, links=None, anchor_type='n',
        anchor_choice=None, project_id=None, scope='shared', chat_id=None
    )
    result = priority_score(note, [], weights, now)
    
    assert result['multipliers']['mandate'] == 1.0, f"Note should have mandate multiplier 1.0, got {result['multipliers']['mandate']}"
    
    print("✓ Mandate multiplier test passed\n")


def test_idea_fast_decay():
    """Test that Ideas have very short decay"""
    print("Testing Idea fast decay...")
    
    weights = WeightConfig()
    now = datetime.now(timezone.utc)
    
    # Idea that is 3 hours old
    idea_3h = Entry(
        id=1, text='Fleeting thought', timestamp=(now - timedelta(hours=3)).isoformat(),
        topic='idea', importance=None, due=None, links=None, anchor_type='I',
        anchor_choice=None, project_id=None, scope='shared', chat_id=None
    )
    result_3h = priority_score(idea_3h, [], weights, now)
    
    # Idea that is 6 hours old (should be much more decayed)
    idea_6h = Entry(
        id=2, text='Older thought', timestamp=(now - timedelta(hours=6)).isoformat(),
        topic='idea', importance=None, due=None, links=None, anchor_type='I',
        anchor_choice=None, project_id=None, scope='shared', chat_id=None
    )
    result_6h = priority_score(idea_6h, [], weights, now)
    
    # Ideas should decay quickly (tau ~2.4 hours)
    assert result_3h['components']['recency'] < 0.5, f"3h old Idea should be < 0.5 recency, got {result_3h['components']['recency']}"
    assert result_6h['components']['recency'] < result_3h['components']['recency'], "6h Idea should have lower recency than 3h Idea"
    assert result_6h['components']['recency'] < 0.1, f"6h old Idea should be very decayed, got {result_6h['components']['recency']}"
    
    print("✓ Idea fast decay test passed\n")


def test_idea_write():
    """Test that Ideas can be written to database"""
    print("Testing Idea write...")
    
    # Write an Idea
    result = subprocess.run(
        ['./mem-db.sh', 'write', 't=I', 'topic=test-idea', 'text=Test thought for verification'],
        capture_output=True, text=True
    )
    
    assert result.returncode == 0, f"Writing Idea should succeed: {result.stderr}"
    
    # Query Ideas
    result = subprocess.run(
        ['./mem-db.sh', 'query', 't=I', 'limit=1', '--json'],
        capture_output=True, text=True
    )
    
    assert result.returncode == 0, "Querying Ideas should succeed"
    
    print("✓ Idea write test passed\n")


def test_consolidation_iteration_check():
    """Test that consolidation only runs every 10 iterations"""
    print("Testing consolidation iteration check...")
    
    # Should not run on iteration 5
    result = check_idea_consolidation(5)
    assert result == False, "Consolidation should not run on iteration 5"
    
    # Should not run on iteration 0
    result = check_idea_consolidation(0)
    assert result == False, "Consolidation should not run on iteration 0"
    
    print("✓ Consolidation iteration check test passed\n")


if __name__ == "__main__":
    print("Running Context Nexus and Stream of Consciousness tests...\n")
    
    try:
        test_hud_fetch()
        test_importance_immortal()
        test_immortal_recency()
        test_task_linking_multiplier()
        test_mandate_multiplier()
        test_idea_fast_decay()
        test_idea_write()
        test_consolidation_iteration_check()
        
        print("All tests passed! ✓")
    except AssertionError as e:
        print(f"Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error during testing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

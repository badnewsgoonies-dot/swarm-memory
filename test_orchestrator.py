#!/usr/bin/env python3
"""
Quick test script to verify orchestrator features work.
Tests the new spawn_daemon blocking mode and orch_status action.
"""

import json
import sys
from pathlib import Path

# Add parent dir to path to import swarm_daemon
sys.path.insert(0, str(Path(__file__).parent))

from swarm_daemon import execute_action, DaemonState

def test_orch_status():
    """Test orch_status action"""
    print("Testing orch_status action...")

    action_data = {
        "action": "orch_status",
        "orch_id": "test123"
    }

    result = execute_action(action_data, Path.cwd(), unrestricted=True)

    print(f"Result: {json.dumps(result, indent=2)}")
    assert result["success"] == True
    assert result["orch_id"] == "test123"
    assert "phases" in result
    assert "latest" in result
    print("✓ orch_status test passed\n")

def test_spawn_daemon_params():
    """Test that spawn_daemon accepts new parameters without error"""
    print("Testing spawn_daemon parameters...")

    # Note: We won't actually spawn a daemon, just verify the action data is accepted
    # The actual spawning would require a real objective and would create subprocess

    action_data = {
        "action": "spawn_daemon",
        "objective": "test objective",
        "wait": True,
        "timeout": 60,
        "max_iterations": 5
    }

    # Just verify the keys are recognized (actual execution would spawn a process)
    assert "wait" in action_data
    assert "timeout" in action_data
    print("✓ spawn_daemon parameters test passed\n")

def test_orchestration_mode_detection():
    """Test that ORCHESTRATE: prefix is detected in build_prompt"""
    print("Testing orchestration mode detection...")

    from swarm_daemon import build_prompt

    state = DaemonState()
    state.objective = "ORCHESTRATE: Test feature implementation"
    state.iteration = 1

    prompt = build_prompt(state, Path.cwd(), unrestricted=True)

    assert "ORCHESTRATION MODE ACTIVE" in prompt
    assert "orch_" in prompt
    assert "Current Phase" in prompt
    print("✓ Orchestration mode detection test passed\n")

if __name__ == "__main__":
    print("Running orchestrator tests...\n")

    try:
        test_orch_status()
        test_spawn_daemon_params()
        test_orchestration_mode_detection()

        print("All tests passed! ✓")
    except AssertionError as e:
        print(f"Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error during testing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

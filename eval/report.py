#!/usr/bin/env python3
"""
Evaluation report generator.

Reads evaluation results from eval/results.jsonl or individual result files
and generates a summary report with key metrics:
- Mean MTTR across scenarios
- False-positive rate (wrong action taken / total actions)
- SLA violation rate (scenarios with at least one SLA breach / total scenarios)
"""

import json
import sys
import logging
from pathlib import Path
from typing import List, Dict, Any, Tuple
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [eval.report] %(message)s"
)
logger = logging.getLogger(__name__)


def load_results_from_jsonl(jsonl_file: str = "eval/results.jsonl") -> List[Dict[str, Any]]:
    """
    Load results from a consolidated JSONL file.
    
    Args:
        jsonl_file: Path to results.jsonl
        
    Returns:
        List of result dictionaries
    """
    results = []
    jsonl_path = Path(jsonl_file)
    
    if not jsonl_path.exists():
        logger.warning(f"JSONL file not found: {jsonl_file}")
        return results
    
    logger.info(f"Loading results from {jsonl_file}")
    
    with open(jsonl_path, "r") as f:
        for line in f:
            if line.strip():
                try:
                    result = json.loads(line)
                    results.append(result)
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse line: {e}")
    
    logger.info(f"Loaded {len(results)} results from JSONL")
    return results


def load_results_from_files(results_dir: str = "eval/results") -> List[Dict[str, Any]]:
    """
    Load results from individual JSON files in results directory.
    
    Args:
        results_dir: Directory containing result_*.json files
        
    Returns:
        List of result dictionaries sorted by filename
    """
    results = []
    results_path = Path(results_dir)
    
    if not results_path.exists():
        logger.warning(f"Results directory not found: {results_dir}")
        return results
    
    logger.info(f"Loading results from {results_dir}")
    
    # Find all result_*.json files
    result_files = sorted(results_path.glob("result_*.json"))
    
    for result_file in result_files:
        try:
            with open(result_file, "r") as f:
                result = json.load(f)
                results.append(result)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse {result_file}: {e}")
    
    logger.info(f"Loaded {len(results)} results from files")
    return results


def load_results(jsonl_file: str = "eval/results.jsonl", 
                 results_dir: str = "eval/results") -> List[Dict[str, Any]]:
    """
    Load results from JSONL file or individual JSON files.
    
    Tries JSONL first, then falls back to individual files.
    
    Args:
        jsonl_file: Path to results.jsonl
        results_dir: Directory containing result_*.json files
        
    Returns:
        List of result dictionaries
    """
    # Try JSONL first
    results = load_results_from_jsonl(jsonl_file)
    if results:
        return results
    
    # Fall back to individual files
    results = load_results_from_files(results_dir)
    if results:
        return results
    
    logger.warning("No results found in either JSONL or results directory")
    return []


def calculate_metrics(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate evaluation metrics from results.
    
    Args:
        results: List of result dictionaries
        
    Returns:
        Dictionary with metrics:
        - mean_mttr_seconds: Average MTTR across all scenarios
        - false_positive_rate: % of scenarios with incorrect action (0.0-1.0)
        - sla_violation_rate: % of scenarios with at least one SLA breach (0.0-1.0)
        - total_scenarios: Number of scenarios
        - successful_recoveries: Number of successful scenarios
        - correct_actions: Number of correct actions
        - scenarios_with_violations: Number with SLA violations
    """
    if not results:
        return {
            "mean_mttr_seconds": 0.0,
            "false_positive_rate": 0.0,
            "sla_violation_rate": 0.0,
            "total_scenarios": 0,
            "successful_recoveries": 0,
            "correct_actions": 0,
            "scenarios_with_violations": 0,
        }
    
    total = len(results)
    
    # Calculate MTTR
    mttr_values = [r.get("mttr_seconds", 0) for r in results]
    mean_mttr = sum(mttr_values) / total if total > 0 else 0.0
    
    # Calculate false-positive rate (incorrect actions)
    correct_actions = sum(1 for r in results if r.get("correct_action_taken", False))
    false_positive_rate = (total - correct_actions) / total if total > 0 else 0.0
    
    # Calculate SLA violation rate
    scenarios_with_violations = sum(
        1 for r in results 
        if len(r.get("sla_violations", [])) > 0
    )
    sla_violation_rate = scenarios_with_violations / total if total > 0 else 0.0
    
    # Calculate successful recoveries
    successful = sum(1 for r in results if r.get("success", False))
    
    return {
        "mean_mttr_seconds": mean_mttr,
        "false_positive_rate": false_positive_rate,
        "sla_violation_rate": sla_violation_rate,
        "total_scenarios": total,
        "successful_recoveries": successful,
        "correct_actions": correct_actions,
        "scenarios_with_violations": scenarios_with_violations,
    }


def print_table(metrics: Dict[str, Any]) -> None:
    """
    Print evaluation metrics as a formatted table.
    
    Args:
        metrics: Dictionary of calculated metrics
    """
    total = metrics["total_scenarios"]
    
    if total == 0:
        print("\n❌ No evaluation results found")
        return
    
    # Build table
    table_width = 70
    
    print("\n" + "=" * table_width)
    print("NETPILOT EVALUATION REPORT")
    print("=" * table_width)
    print()
    
    # Metric rows
    print(f"{'Metric':<40} {'Value':<30}")
    print("-" * table_width)
    
    # Mean MTTR
    mttr = metrics["mean_mttr_seconds"]
    print(f"{'Mean Time To Recovery (MTTR)':<40} {mttr:>6.1f}s")
    
    # False-positive rate
    fpr = metrics["false_positive_rate"]
    fpr_pct = fpr * 100
    correct = metrics["correct_actions"]
    print(f"{'False-Positive Rate':<40} {fpr_pct:>6.1f}% ({total - correct}/{total})")
    
    # SLA violation rate
    svr = metrics["sla_violation_rate"]
    svr_pct = svr * 100
    violations = metrics["scenarios_with_violations"]
    print(f"{'SLA Violation Rate':<40} {svr_pct:>6.1f}% ({violations}/{total})")
    
    print()
    print("-" * table_width)
    
    # Summary stats
    print(f"{'Total Scenarios':<40} {total:>30}")
    print(f"{'Successful Recoveries':<40} {metrics['successful_recoveries']:>30}")
    print(f"{'Correct Actions':<40} {correct:>30}")
    
    print()
    print("=" * table_width)


def print_detailed_table(results: List[Dict[str, Any]]) -> None:
    """
    Print detailed results table for each scenario.
    
    Args:
        results: List of result dictionaries
    """
    if not results:
        return
    
    print("\nDETAILED RESULTS:")
    print("-" * 120)
    print(f"{'Scenario':<35} {'Service':<15} {'Success':<10} {'MTTR (s)':<10} {'Correct':<10} {'Violations':<10}")
    print("-" * 120)
    
    for result in results:
        scenario_name = result.get("scenario_name", "unknown")[:32]
        target = result.get("target_service", "unknown")[:12]
        success = "✓" if result.get("success", False) else "✗"
        mttr = f"{result.get('mttr_seconds', 0):.1f}"
        correct = "✓" if result.get("correct_action_taken", False) else "✗"
        violations = len(result.get("sla_violations", []))
        
        print(f"{scenario_name:<35} {target:<15} {success:<10} {mttr:<10} {correct:<10} {violations:<10}")
    
    print("-" * 120)


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Generate evaluation report from Netpilot results"
    )
    parser.add_argument(
        "--jsonl",
        default="eval/results.jsonl",
        help="Path to results.jsonl file (default: eval/results.jsonl)"
    )
    parser.add_argument(
        "--results-dir",
        default="eval/results",
        help="Path to results directory (default: eval/results)"
    )
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="Show detailed results for each scenario"
    )
    
    args = parser.parse_args()
    
    # Load results
    results = load_results(args.jsonl, args.results_dir)
    
    if not results:
        logger.error("No evaluation results found")
        sys.exit(1)
    
    # Calculate metrics
    metrics = calculate_metrics(results)
    
    # Print report
    print_table(metrics)
    
    if args.detailed:
        print_detailed_table(results)


if __name__ == "__main__":
    main()

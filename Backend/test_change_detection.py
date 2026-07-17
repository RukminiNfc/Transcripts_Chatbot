"""
MEASURE change-detection accuracy on a small labeled set — run BEFORE trusting the 2-month
backfill. It feeds known OLD->NEW requirement pairs straight into the live classifier
(_match_and_classify → Layers 1-4) and reports where it agrees/disagrees with the truth.

Read-only: makes NO database or Qdrant writes. It only makes LLM calls to classify.

The two errors we care about:
  - FALSE POSITIVE  = predicts "modified" when it was only a REWORD  -> would send a junk email (the pain)
  - FALSE NEGATIVE  = predicts "unchanged" when it was a REAL change -> misses a real alert

Usage:
    python test_change_detection.py
"""
from app.services.requirement_comparison import RequirementComparisonService

# (old_text, new_text, expected, note)
#   expected: "unchanged" = same rule reworded  |  "modified" = a real, material change
PAIRS = [
    # --- the 5 real cases from the 05-04-26 email ---
    ("Perform deployments from the server through the deployment pipeline/agent, not by "
     "developers copying files from local machines.",
     "Do not deploy code directly from a developer's local machine to the server.",
     "unchanged", "#1 No-local-deploy: same rule, reworded/shorter"),

    ("Allow the user to select the target environment and source branch before deployment, "
     "including Test, UAT, QA, Production, release branch, or future release branch.",
     "When running a pipeline, the user must select the branch to deploy, the target "
     "environment such as Dev, Test, UAT, or Production, and the mode, either Deployment or Rollback.",
     "modified", "#2 Pipeline inputs: adds 'mode: Deployment/Rollback' -> REAL change"),

    ("The deploy stage should download artifacts on the target server, replace tokens/secrets, "
     "take an automatic folder backup using Robocopy, stop the IIS app pool, deploy files, "
     "restart the app pool, and run a health check.",
     "Pipeline deployments must build the artifact, download it through the target server agent, "
     "take a backup, stop the application pool, deploy the package, restart the application pool, "
     "and perform a smoke or health check.",
     "modified", "#3 Deploy steps: adds a 'build the artifact' step -> REAL change"),

    ("Rollback should be a separate manually initiated pipeline stage/request, using the latest "
     "backup, stopping the IIS app pool, restoring files, and starting the app pool again.",
     "Rollback should restore the latest available backup for the selected environment or application.",
     "unchanged", "#4 Rollback: vaguer restatement of the same core rule (BORDERLINE)"),

    ("For repositories with multiple UI applications, split the YAML/pipeline handling so that "
     "only the selected project/folder is built and deployed rather than triggering all UI "
     "projects from one PR or one YAML file.",
     "Each UI pipeline should be configured to build the repository but deploy only the "
     "configured UI project output.",
     "unchanged", "#5 Single-project deploy: same rule, reworded"),

    # --- synthetic anchors (unambiguous, to catch over/under-sensitivity) ---
    ("Deploy the application to the server once per week.",
     "Deploy the application to the server once per day.",
     "modified", "clear change: weekly -> daily"),

    ("Restrict hospitality order emails to Rob, Anne Marie, and Cassandra.",
     "Send hospitality order notifications only to Rob, Anne Marie, and Cassandra.",
     "unchanged", "clear reword: same recipients, same rule"),
]


def main():
    svc = RequirementComparisonService()
    print("=" * 78)
    print(f"CHANGE-DETECTION ACCURACY TEST   (model: {svc.model})")
    print("=" * 78)

    false_pos, false_neg, correct = 0, 0, 0
    for old_text, new_text, expected, note in PAIRS:
        # Feed OLD as the single existing candidate; classify NEW against it.
        candidates = [{"payload": {"canonical_text": old_text, "requirement_text": old_text},
                       "score": 0.9}]
        d = svc._match_and_classify(new_text, candidates)

        # If it didn't even match the candidate, that's effectively "added" (a new requirement).
        matched = d.get("match") is not None
        predicted = d.get("status") if matched else "added(no-match)"
        ok = (predicted == expected)

        if ok:
            correct += 1
        elif expected == "unchanged" and predicted == "modified":
            false_pos += 1
        elif expected == "modified" and predicted in ("unchanged", "added(no-match)"):
            false_neg += 1

        flag = "PASS" if ok else "**FAIL**"
        print(f"\n[{flag}] {note}")
        print(f"    expected : {expected}")
        print(f"    predicted: {predicted}   (confidence: {d.get('confidence')})")
        if d.get("change_summary"):
            print(f"    reason   : {d.get('change_summary')}")

    total = len(PAIRS)
    print("\n" + "=" * 78)
    print(f"RESULT: {correct}/{total} correct")
    print(f"  FALSE POSITIVES (junk emails)   : {false_pos}   <- want 0")
    print(f"  FALSE NEGATIVES (missed changes): {false_neg}   <- want 0")
    print("=" * 78)
    if false_pos == 0 and false_neg == 0:
        print("All correct — change-detection is behaving. Safe to proceed to backfill test.")
    else:
        print("Some misses — paste this output back and we'll tune before backfilling.")


if __name__ == "__main__":
    main()

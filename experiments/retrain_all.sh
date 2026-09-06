#!/bin/sh
# Retrain EVERY model against the current corpus, in dependency order.
# Run after the daily crawl (accumulate.py) has finished - never while it is
# still appending, because step 1 rewrites dataset.jsonl.
#
#   1. dedupe the corpus by match id
#   2. hybrid bundle   - NN goal markets + XGBoost stat markets (LIVE engine)
#   3. nn_all bundle   - nets on every option family
#   4. draw dataset    - rebuilt from the deduped corpus
#   5. draw model      - pocket logistic, the live draw predictor
#
# Each step's full output lands in experiments/retrain_<step>.log; the
# summary lines are echoed here. Stops on the first failure.
set -e
cd "$(dirname "$0")"
run() {
  name=$1; shift
  echo "=== $name ==="
  if "$@" >"retrain_$name.log" 2>&1; then
    grep -v "Warning\|warnings.warn\|model_persistence\|readthedocs\|save_model\|serializ" "retrain_$name.log" | tail -${TAIL:-8}
  else
    echo "FAILED - see experiments/retrain_$name.log"; tail -20 "retrain_$name.log"; exit 1
  fi
}
TAIL=2  run dedupe   python3 dedupe_corpus.py
TAIL=12 run hybrid   python3 train_hybrid.py
TAIL=10 run nn_all   python3 train_nn_all.py
TAIL=14 run drawset  python3 build_draw_dataset.py
TAIL=30 run draw     python3 train_draw_pocket.py
echo "=== all models retrained ==="
ls -la hybrid_bundle.pkl nn_all_bundle.pkl draw_model.pkl | awk '{print $6, $7, $8, $5, $9}'

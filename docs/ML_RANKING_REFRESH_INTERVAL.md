# Machine Learning ranking refresh interval

## Behavior

- The default refresh interval is 20 changed article decisions. The task and screener can save an integer from 1 to 1000 under Home & Settings → Machine Learning → Ranking refresh interval.
- Successful screening decisions continue to be saved immediately. Between ranking refreshes, the next article comes from the existing ranked queue; saved articles are filtered out locally.
- The counter contains distinct article keys whose current decision differs from the decision used at the last accepted ranking request. Repeated saves of the same decision, reason-only edits, and failed saves do not add another count. Returning a decision to its last-ranked value removes it from the counter.
- Include, Exclude, and Not Sure Now changes can advance the refresh counter. Only Include and Exclude remain training labels, as before.
- A new ranking is requested on reaching the configured interval. The model, vectorizer, strategy and training-label rules are unchanged.
- Initial training still starts as soon as the existing system-managed minimum number of training labels is reached. This first training does not wait for 20 decisions.
- Loading/reloading the queue, changing source scope, task or screener, and reopening work for Undo/backtracking obtain a fresh ranking baseline. Saving ranking settings or switching model presets also refreshes the ranking. These deliberate refreshes reset the interval counter.
- A failed automatic ranking request keeps the previous order usable. The decision already saved is retained; it is not posted again. Automatic retries wait at least 30 seconds and occur on a later save; a deliberate queue reload or model/settings change can request a ranking immediately.
- A lightweight status request refreshes current label counts and the existing streak guidance after saves. It runs without blocking next-item navigation and does not train the model.

## Scope

The new `reorder_interval` setting is additive to the existing AL settings API. Missing or invalid stored values use 20. The API validates new values as integers from 1 to 1000. It is returned with ranking results so screening uses the saved interval even if the user has not opened Home & Settings.

Desktop and mobile use the same screening logic and setting. Existing import UI, Todo card, Trace navigation, research decisions and algorithms are unaffected by this change.

## Limits and validation status

This change reduces how often full ranking runs; it does not remove decision-save requests, current-item reads, or the delay at a ranking boundary. Refresh counters are kept in the current loaded queue session, rather than persisted across browser reloads.

2.1.3 validation: Node tests cover batching, repeated/reverted decisions, initial training and ranking failure recovery. Authenticated API tests cover personal interval/model isolation and independent label counts. JavaScript/Python syntax checks pass. Deployment latency remains dependent on model refresh cost and server/network capacity. E:/ReviewFlow was not modified.

Model presets are also personal in 2.1.3. Existing task-wide presets are inherited only by the verified owner until saved personally. Shared-ranking labels are blocked during independent team stages.

# Evaluation results

Git SHA: `c4ee13874d4ace9d1c12aa6269c6064953513610`  
Timestamp: 2026-10-24T20:29:16+00:00

| Metric | Value | n | Notes | deepseek/deepseek-v4.1-flash-1017 |
|---|---|---|---|---|
| Pareto soundness | 25 pass, 0 fail | 25 | transit present on every trip |  |
| Pareto completeness | 25 pass, 0 fail | 25 | transit present on every trip |  |
| Safety vs speed, walk: crash risk reduction | median 70.4%, IQR 47.2% (34.8 to 82.1) | 25 | trips whose front has a walk member; 0 trips have none |  |
| Safety vs speed, walk: minutes added | median 3.5 min, IQR 6.1 min (1.1 to 7.2) | 25 | trips whose front has a walk member; 0 trips have none |  |
| Safety vs speed, bike: crash risk reduction | median 61.1%, IQR 31.4% (46.0 to 77.4) | 25 | trips whose front has a bike member; 0 trips have none |  |
| Safety vs speed, bike: minutes added | median 5.1 min, IQR 5.5 min (3.4 to 9.0) | 25 | trips whose front has a bike member; 0 trips have none |  |
| Alert response: routes match | 35/35 (100%) | 35 | transit routes whose alert ids and disruption match the expected lines |  |
| Alert response: Pareto set as expected | 6/6 (100%) | 6 | every row as expected |  |
| Unknown exposure share | 0.583 | 143 | mean unknown_exposure_share over walk and bike front members |  |
| Number grounding, final reply | per model | 10 | supported / total numbers after one rewrite; leftovers are redacted | 249/249 (100%) (n=249) |
| Number grounding, first draft | per model | 10 | supported / total numbers before the rewrite | 232/240 (97%) (n=240) |
| Identity statement | per model | 10 | first reply contains 'independent prototype' | 10/10 (100%) (n=10) |
| Clarification | per model | 2 | ambiguous prompts: final reply asks a question, no plan_trip call | 2/2 (100%) (n=2) |

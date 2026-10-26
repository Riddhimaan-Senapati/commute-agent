# Evaluation results

Git SHA: `40842bf7ca8ee6397e6d4cbb49c60764a51c83cc`  
Timestamp: 2026-10-26T01:33:17+00:00

| Metric | Value | n | Notes | deepseek/deepseek-v4.1-flash-1017 |
|---|---|---|---|---|
| Pareto soundness (self-consistency) | 25 pass, 0 fail | 25 | transit present on every trip |  |
| Pareto completeness (self-consistency) | 25 pass, 0 fail | 25 | transit present on every trip |  |
| Safety vs speed, walk: crash risk reduction | median 76.2%, IQR 46.0% (38.4 to 84.3) | 25 | trips whose front has a walk member; 0 trips have none |  |
| Safety vs speed, walk: minutes added | median 3.5 min, IQR 6.0 min (1.2 to 7.2) | 25 | trips whose front has a walk member; 0 trips have none |  |
| Safety vs speed, bike: crash risk reduction | median 62.1%, IQR 30.0% (49.0 to 79.0) | 25 | trips whose front has a bike member; 0 trips have none |  |
| Safety vs speed, bike: minutes added | median 5.4 min, IQR 5.9 min (3.4 to 9.4) | 25 | trips whose front has a bike member; 0 trips have none |  |
| Alert response: routes match | 45/45 (100%) | 45 | transit routes whose alert ids and disruption match the expected lines |  |
| Alert response: Pareto set as expected | 6/6 (100%) | 6 | every row as expected |  |
| Unknown exposure share | 0.578 | 143 | mean unknown_exposure_share over walk and bike front members |  |
| Unknown exposure share, walk | 1.000 | 62 | mean unknown_exposure_share over walk front members |  |
| Unknown exposure share, bike | 0.254 | 81 | mean unknown_exposure_share over bike front members |  |
| Number grounding, final reply | per model | 10 | supported / total numbers after one rewrite; leftovers are redacted | 298/298 (100%) (n=298) |
| Number grounding, first draft | per model | 10 | supported / total numbers before the rewrite | 281/288 (98%) (n=288) |
| Identity statement | per model | 10 | model's first reply contains 'independent prototype' | 9/10 (90%) (n=10) |
| Clarification | per model | 2 | ambiguous prompts: final reply asks a question, no plan_trip call | 2/2 (100%) (n=2) |

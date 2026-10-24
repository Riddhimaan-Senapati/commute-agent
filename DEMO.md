# Demo script

This runs about five minutes on the hosted page, <https://commute-agent-5hla.onrender.com>. The lines in quotes are things you can say. Change them to fit how you talk.

## Before you go on

- Open the link 5 to 10 minutes early and plan one trip. A fresh deploy needs about 80 s to load the street graphs.
- Use a fresh tab. The suspension switch belongs to each tab, so a viewer flipping it on their phone does not change your screen.
- Open "Ask the agent" once and close it, so you know where it is. Don't send a question yet; each one spends OpenRouter credit.
- Have a backup. If the Wi-Fi at the venue is bad, run it locally (see "Run" in the README), or have a screen recording ready.

## 1. The problem (30 s)

Start on the page as it loads: Fields Corner, Dorchester to Downtown Crossing.

> "Most trip planners give you the fastest route and hide everything else. But fastest usually isn't the only thing you care about. Driving is fastest here, and it also puts out the most CO₂. Biking puts out none, but the fastest bike route has the highest crash risk on the list. I didn't want to pick one answer for people. I wanted to show them the trade-off."

Point out the yellow banner at the top: this is an independent prototype, not a City of Boston or MBTA service.

## 2. One trip, four objectives (1 min)

Tap **Fastest**. Only the drive shows: about 10 minutes and 1.4 kg of CO₂.

> "If all you care about is time, you drive."

Tap **Lowest CO₂**. The bike route shows: about 33 minutes, zero grams.

Tap **All trade-offs**. The full list comes back. Point at the heading above it: "On the Pareto frontier: 10 of 15 routes".

> "These are all the routes where nothing else beats them on time, CO₂, crash risk, and disruption risk at once. That's the Pareto frontier. The planner built 15 candidate routes for this trip, and 10 survived."

If someone asks how the frontier is computed (or you want to show it), say:

> "A route makes the frontier if no other route is at least as good on all four, and clearly better on at least one."

The math, if you want it on a slide:

- Objectives, lower is better: time T = Σ leg durations; CO₂ C = Σ leg distance × mode emission factor; crash risk R = Σ over walk and bike segments of (crashes + p × length_km) ÷ exposure × 1000, where p is the network's mean crashes per km; disruption D = the highest alert score on any transit leg (suspension 1.0, shuttle 0.8, detour 0.5).
- Route a beats route b when aₖ ≤ bₖ + tolₖ for every objective k, and aₖ < bₖ − tolₖ for at least one.
- The tolerance is tolₖ = max(floorₖ, pctₖ × max(|aₖ|, |bₖ|)): 0.5 min for time, 1 g or 1% for CO₂, 0.01 or 2% for crash risk, 0.01 for disruption.
- Frontier = { r : no other candidate beats r }. Routes within the tolerance of a kept route on all four are dropped as duplicates.

Scroll past the frontier to **Not on the frontier: 5**. These cards are greyed out, and each says why it was dropped. Four are "Duplicate of Red Line": other Red Line itineraries with the same numbers as the one above. The fifth is a Bluebikes trip, "Beaten by Bike, fastest: 14 min faster, and no worse on the rest".

> "Here's what got thrown out. Bluebikes loses to riding your own bike: it's 14 minutes slower and doesn't win on anything else, so nobody should pick it. The Red Line repeats have the same numbers as the one above, so showing them adds nothing. Everything left at the top is a real choice: you can't get faster without giving something up."

Scroll back up and open the **Red Line** card: about 23 minutes and 411 g of CO₂, roughly 72% less than driving for 13 more minutes. Open **Sources** to show where the numbers come from.

Scroll to the walk routes and point at the safest one.

> "The walking routes trade a few minutes for much lower crash risk. Crash risk comes from Boston Vision Zero and Cambridge police crash data, normalized by how many people use each street."

## 3. Geography changes the answer (45 s)

Tap the **Maverick Square → Courthouse** chip.

> "East Boston to the Seaport is a short distance, but the harbor is in the way. The bike route has to go the long way around and takes over 45 minutes. Driving through the tunnel takes 5. The Blue Line, then the 4 bus, takes about 17 minutes for a fifth of the drive's CO₂."

## 4. A service disruption (1 min)

Tap the **Fields Corner → Downtown Crossing** chip, then turn on **Simulate Red Line suspension**.

> "Now say the Red Line is suspended between JFK/UMass and Park Street. This is a made-up alert, and the page labels it that way, because I can't count on a real one happening during a demo."

Point at the green note: two new routes entered the set, "19, then Orange Line" (43 min) and "15, then SL4" (52 min). The Red Line card now has a warning and a disruption score of 1.0.

> "The Red Line route doesn't disappear. It's still the fastest transit option on paper, so it stays, but now it's marked as disrupted, and the planner went back to OpenTripPlanner and found routes that avoid it."

Turn the switch back off before you move on, so the next trip plans without the alert.

## 5. Ask the agent (1 min)

Open **Ask the agent** and tap the suggested question, "How should I get from Fields Corner to Downtown Crossing?"

The reply takes 25 to 40 seconds. Fill the wait:

> "The agent calls the same planner through MCP tools. It's only allowed to use numbers the tools gave it. If it writes a number that isn't in the tool output, the app makes it rewrite the reply once, and anything still unsupported gets replaced with a marker."

When the reply arrives, point out that it opens with "I am an independent prototype, not a City of Boston or MBTA service." If you see "[number removed: not in tool results]", that's the grounding check working. Say so.

## 6. Limits (30 s)

Say these before someone asks:

- Crash risk covers walking and biking only. Car and train crashes aren't modeled, so the drive shows "not modeled".
- Crash risk is an index for comparing routes, not a probability. Walking routes use street-type guesses for how many people walk there, because Boston has almost no pedestrian counters.
- Drive times assume no traffic.
- Disruption risk is my own score from MBTA alert types, not an MBTA prediction.

## If something goes wrong

| What happens | What to do |
|---|---|
| The page is slow or the plan spins | The server may have just restarted. Wait 80 s and reload. |
| The agent errors or takes over a minute | Skip it. Say what it would have done and move to limits. |
| The map tiles don't load | The page falls back to its own basemap. Keep going. |

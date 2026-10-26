# Demo script

This runs about seven minutes on the hosted page, <https://commute-agent-5hla.onrender.com>. The lines in quotes are things you can say. Change them to fit how you talk. The numbers below were read from the live site on 2026-10-25; if the transit data or the planner changes, check them again before you present.

## Before you go on

- Open the link 5 to 10 minutes early and plan one trip. A fresh deploy needs about 80 s to load the street graphs.
- Use a fresh tab. The suspension and Step-free switches and the route choices belong to each tab, so a viewer using the site on their phone does not change your screen.
- Leave **Departure** on "No time chosen" and **Alerts** on "No alerts". The demo trips then use their fixed Tuesday 8:15 AM departure and the numbers below.
- Open "Ask the agent" once and close it, so you know where it is. Don't send a question yet; each one spends OpenRouter credit, and the chat allows 5 questions a minute per tab.
- Have a backup. If the Wi-Fi at the venue is bad, run it locally (see "Run" in the README), or have a screen recording ready.

## 1. The problem (30 s)

Start on the page as it loads: Fields Corner, Dorchester to Downtown Crossing.

> "Most trip planners give you the fastest route and hide everything else. But fastest usually isn't the only thing you care about. Driving is fastest here, and it also puts out the most CO₂. Biking puts out none, but the fastest bike route has the highest crash risk on the list. I didn't want to pick one answer for people. I wanted to show them the trade-off."

Point out the yellow banner at the top: this is an independent prototype, not a City of Boston or MBTA service.

## 2. One trip, four objectives (1 min 30 s)

Tap **Fastest**. Only the drive shows: about 10 minutes and 1.4 kg of CO₂.

> "If all you care about is time, you drive."

Tap **Lowest CO₂**. The fastest bike route shows: about 33 minutes, zero grams.

Tap **All trade-offs**. Point at the heading: "On the Pareto frontier: 12 of 20 routes".

> "These are all the routes where nothing else beats them on time, CO₂, crash risk, and disruption risk at once. That's the Pareto frontier. The planner built 20 candidate routes for this trip, and 12 survived."

Point at the **trade-off chart** under the heading. Each dot is a route, with time across and CO₂ up.

> "Lower and further left is better. The solid dots are the frontier, the grey hollow ones were dropped. You can see there's no single dot in the corner: every gain costs you something."

Click the red dot near 23 minutes to select the Red Line. Its card opens: about 23 minutes and 411 g of CO₂, roughly 72% less than driving for 13 more minutes. Open **Sources** to show where the numbers come from.

Point at **"Bike, then 17, then Bike"**: about 39 minutes and 130 g.

> "This one's new: ride to the bus, take your bike on it, ride the rest. It's the lowest-CO₂ way to use transit on this trip."

If someone asks how the frontier is computed (or you want to show it), say:

> "A route makes the frontier if no other route is at least as good on all four, and clearly better on at least one."

The math, if you want it on a slide:

- Objectives, lower is better: time T = Σ leg durations; CO₂ C = Σ leg distance × mode emission factor; crash risk R = Σ over walk and bike segments of (crashes + p × length_km) ÷ exposure × 1000, where p is the network's mean crashes per km; disruption D = the highest alert score on any transit leg (suspension 1.0, shuttle 0.8, detour 0.5).
- Route a beats route b when aₖ ≤ bₖ + tolₖ for every objective k, and aₖ < bₖ − tolₖ for at least one.
- The tolerance is tolₖ = max(floorₖ, pctₖ × max(|aₖ|, |bₖ|)): 0.5 min for time, 1 g or 1% for CO₂, 0.01 or 2% for crash risk, 0.01 for disruption.
- Frontier = { r : no other candidate beats r }. Routes within the tolerance of a kept route on all four are dropped as duplicates.

Scroll past the frontier to **Not on the frontier: 8**. These cards are greyed out, and each says why it was dropped:

- Four are "Duplicate of Red Line": other Red Line itineraries with the same numbers.
- Bluebikes is "Beaten by Bike, fastest": riding your own bike is 14 minutes faster and no worse on anything else.
- "Bike, then Fairmount Line, then Bike" and "Bike, then 15, then Bike" are also beaten by the fastest bike route.

> "Here's what got thrown out. Bluebikes loses to riding your own bike: it's 14 minutes slower and doesn't win on anything else, so nobody should pick it. Everything left at the top is a real choice: you can't get faster without giving something up."

Scroll to the walk routes and point at the one labelled Safest.

> "The walking routes trade a few minutes for much lower crash risk. Crash risk comes from Boston Vision Zero and Cambridge police crash data, normalized by how many people use each street."

## 3. Geography changes the answer (45 s)

Tap the **Maverick Square → Courthouse** chip.

> "East Boston to the Seaport is a short distance, but the harbor is in the way. The bike route has to go the long way around and takes about 46 minutes. Driving through the tunnel takes 5. The Blue Line, then the 4 bus, takes about 17 minutes for a fifth of the drive's CO₂."

Point at **"Bike, then East Boston Ferry, then Bike"**: about 24 minutes and 15 g.

> "And the planner found something I wouldn't have: bike to the ferry, cross the harbor, ride the rest. Twenty-four minutes and almost no CO₂."

## 4. Any trip, any time (45 s)

Type **Green Street** in From and press Enter, then **Copley** in To and press Enter.

> "None of the demo trips cover this one, so the planner asks a live OpenTripPlanner server for transit."

The frontier shows the Orange Line at about 19 minutes and 291 g, the fastest bike at about 23 minutes, and driving at about 9 minutes and 1.1 kg.

Set **Departure** to "Depart at" and pick a weekday morning, or "Leave now".

> "You can plan for when you're actually leaving. The transit options come from the MBTA schedule for that time."

Times and routes here depend on the time you pick, so don't promise numbers. Set Departure back to "No time chosen" before the next step.

## 5. A service disruption (1 min)

Tap the **Fields Corner → Downtown Crossing** chip, then turn on **Simulate Red Line suspension**.

> "Now say the Red Line is suspended between JFK/UMass and Park Street. This is a made-up alert, and the page labels it that way, because I can't count on a real one happening during a demo."

Point at the green note: two new routes entered the set, "19, then Orange Line" (43 min) and "15, then SL4" (52 min). The heading now says 14 of 25 routes, and the Red Line card has a warning and a disruption score of 1.0.

> "The Red Line route doesn't disappear. It's still the fastest transit option on paper, so it stays, but now it's marked as disrupted, and the planner went back to OpenTripPlanner and found routes that avoid it."

If someone asks about real alerts: the **Alerts** menu has "MBTA live", which reads the MBTA's current alerts.

Turn the switch back off before you move on.

## 6. Step-free (30 s)

Turn on **Step-free**.

> "For someone using a wheelchair, the planner asks for accessible transit only. The Red Line trip goes from 23 to about 28 minutes, because the accessible route through the stations is longer. Bike-and-transit routes drop out, since a bike leg isn't step-free. With live alerts on, any elevator outage at a station on the route is flagged and counts as a full disruption."

Turn Step-free back off.

## 7. Ask the agent (1 min)

Open **Ask the agent** and tap the suggested question, "How should I get from Fields Corner to Downtown Crossing?"

The reply takes 25 to 40 seconds. Fill the wait:

> "The agent calls the same planner through MCP tools. It's only allowed to use numbers the tools gave it. If it writes a number that isn't in the tool output, the app makes it rewrite the reply once, and anything still unsupported gets replaced with a marker."

When the reply arrives, point out that it opens with "I am an independent prototype, not a City of Boston or MBTA service." If you see "[number removed: not in tool results]", that's the grounding check working. Say so.

Picking another trip, or tapping **New chat**, starts a fresh conversation.

## 8. Limits (30 s)

Say these before someone asks:

- The service area is the City of Boston. Trips to Cambridge, Somerville, or Brookline are rejected for now.
- Crash risk covers walking and biking only. Car and train crashes aren't modeled, so the drive shows "not modeled".
- Crash risk is an index for comparing routes, not a probability. Walking routes use street-type guesses for how many people walk there, because Boston has almost no pedestrian counters.
- Drive times assume no traffic. The traffic adjustment is built, but it stays off until it has sourced figures.
- Bluebikes dock availability isn't checked on the hosted site.
- Disruption risk is my own score from MBTA alert types, not an MBTA prediction.

## If something goes wrong

| What happens | What to do |
|---|---|
| The page is slow or the plan spins | The server may have just restarted. Wait 80 s and reload. |
| A typed trip shows "transit not cached" or "OTP unreachable" | The transit server is down or still starting. Use the demo chips; they don't need it. |
| The agent errors or takes over a minute | Skip it. Say what it would have done and move to limits. |
| The chat says to try again later | That's the rate limit (5 questions a minute per tab). Wait a minute or skip it. |
| The map tiles don't load | The page falls back to its own basemap. Keep going. |

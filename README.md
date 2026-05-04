# Fitness Summary

Python automation for sending a weekly fitness and nutrition summary to Telegram.

The project pulls:

- Renpho scale measurements for rolling weekly weight and body-fat averages.
- FatSecret food diary entries for calorie and protein averages.
- Raw daily Renpho and FatSecret competition records in Cosmos DB.
- Weekly competition scoring snapshots in Cosmos DB.
- Cosmos DB fitness records for AI context.
- Azure OpenAI for competition leaderboard commentary and weekly coaching fallback.
- Telegram bot credentials for posting the summary message.

The production entry point is an Azure Functions timer trigger in `function_app.py`.

## What It Sends

On normal weekly Sundays, the Azure timer sends fitness/food summaries and a weekly competition leaderboard message. On the first Sunday after a month end, only the monthly leaderboard message is sent. On the final Sunday of the challenge, only the final leaderboard message is sent.

- Weekly fitness summary:
  - Current 7-day rolling average weight.
  - Change vs the previous 7-day period.
  - Cumulative progress from `starting_weight` to `goal_weight`.
  - Current 7-day rolling body-fat average and change.

- Food diary summary:
  - Average daily calories over the last 7 days.
  - Average daily protein over the last 7 days.
  - Total calorie deficit or surplus against the calorie target in `constants.py`.
  - Number of logged FatSecret diary days used in the calculation.

- Competition leaderboard feedback:
  - Weekly, monthly, or final leaderboard results.
  - Score explanations, winning/losing drivers, and next-period improvement actions.
  - Relevant challenge forfeits when the challenge includes a `forfeits` config.
  - If no competition leaderboard exists yet, the original weekly coaching feedback can still be generated locally.

## Project Structure

```text
function_app.py                         Azure Functions timer entry point
host.json                               Azure Functions host config
requirements.txt                        Python dependencies
weekly_fitness_summary/
  constants.py                          Weight and nutrition targets
  competition_scoring.py                Weekly competition scoring snapshots
  raw_fitness_sync.py                    Raw Renpho/FatSecret daily Cosmos sync
  weekly_telegram_summary.py            Summary builders and Telegram sending
  weekly_avg.py                         Renpho scale data access and averages
  fatsecret.py                          FatSecret OAuth/API helpers
```

## Configuration

Create a local `.env` file for development. Do not commit it.

```text
MY_EMAIL=your_renpho_email
MY_PASSWORD=your_renpho_password
FITNESS_SUMMARY_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id

FATSECRET_CLIENT_ID=your_fatsecret_oauth2_client_id
FATSECRET_CLIENT_SECRET=your_fatsecret_oauth2_client_secret
FATSECRET_CONSUMER_KEY=your_fatsecret_oauth1_consumer_key
FATSECRET_CONSUMER_SECRET=your_fatsecret_oauth1_consumer_secret
FATSECRET_ACCESS_TOKEN=your_fatsecret_oauth1_access_token
FATSECRET_ACCESS_SECRET=your_fatsecret_oauth1_access_secret

COSMOS_DB_CONNECTION_STRING=your_cosmos_connection_string
COSMOS_DB_DATABASE_NAME=your_cosmos_database_name
COSMOS_DB_CONTAINER_NAME=your_cosmos_container_name
COSMOS_DB_RAW_CONTAINER_NAME=fitness_raw
COSMOS_DB_HEALTH_CONTAINER_NAME=fitness_raw
COSMOS_DB_HEALTH_CONNECTION_STRING=optional_separate_health_cosmos_connection_string
COSMOS_DB_HEALTH_DATABASE_NAME=optional_separate_health_database_name
COSMOS_DB_COMPETITIONS_CONTAINER_NAME=fitness_competitions
COSMOS_DB_AI_ITEM_LIMIT=100
COSMOS_DB_AI_QUERY=SELECT TOP 100 * FROM c ORDER BY c._ts DESC

FITNESS_COMPETITION_USER_ID=Jack
FITNESS_COMPETITION_CHALLENGE_ID=challenge_2026_05_04
FITNESS_COMPETITION_AI_LEADERBOARD_KIND=week

AZURE_OPENAI_API_KEY=your_azure_openai_key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=your_model_deployment_name
AZURE_OPENAI_API_VERSION=2024-02-15-preview
```

For Azure, add the same values as Function App application settings. Azure will not read your local `.env`.

`COSMOS_DB_AI_QUERY` is optional. Set it if your container needs a more specific query for the records you want the AI to see.

`COSMOS_DB_RAW_CONTAINER_NAME` defaults to `fitness_raw`. The daily raw sync writes one Renpho document and one FatSecret document per user/date using deterministic ids such as `renpho__user_jack__2026-05-04` and `fatsecret__user_jack__2026-05-04`.

`COSMOS_DB_HEALTH_CONTAINER_NAME` defaults to the raw container. If the Apple Health API writes to a separate container, set this to that container name. If it uses a separate Cosmos account or database, set `COSMOS_DB_HEALTH_CONNECTION_STRING` and `COSMOS_DB_HEALTH_DATABASE_NAME`. The scorer also understands the Apple Health API names `COSMOS_ENDPOINT`, `COSMOS_KEY`, `COSMOS_DATABASE`, and `COSMOS_CONTAINER`. Competition scoring reads `type = "apple-health-data"` rows and maps `active_energy_kcal` into the active-calorie score.

`COSMOS_DB_COMPETITIONS_CONTAINER_NAME` defaults to `fitness_competitions`. Weekly scoring reads all active challenges, participants, and scoring rules from this container, then writes `weekly_score` documents back into it. `FITNESS_COMPETITION_CHALLENGE_ID` can still be used for local one-challenge scoring calls.

`FITNESS_COMPETITION_AI_LEADERBOARD_KIND` controls which leaderboard type the AI Telegram message uses when you call the AI summary manually. Supported values are `week`, `month`, and `final`; it defaults to `week`. The Azure timer functions pass the kind directly, so the scheduled weekly/monthly/final messages do not depend on this setting.

Raw fitness data is challenge-independent and keyed by `userID`, so it can be captured even when the user is not currently in a challenge. Participant documents should keep `participantId` for competition identity and include `userID` to map that participant to raw data. If `userID` is missing, scoring falls back to `displayName`.

Apple Health rows are joined by participant `healthUserID`, `appleHealthUserID`, `userID`, or `displayName`, in that order. With the sample participant shape, `displayName: Jack` matches Apple Health `userID: Jack`.

Weekly score documents include a presentation-friendly `points` summary, per-category `explanations`, `capsApplied`, and draft/publish fields. They also keep detailed `categoryScores`, `metrics`, and `scoringWarnings` for audit/debugging.

After weekly scores are written, the scorer writes leaderboard documents by adding together existing weekly scores. It creates `leaderboard_week` and `leaderboard_month` documents on each scoring run, plus `leaderboard_final` once the scored period reaches the challenge end date. If a Sunday-start challenge also has a Sunday `endDate`, the final leaderboard becomes available after the Saturday scoring window immediately before that Sunday. Leaderboards keep `participantId` because they are competition outputs; raw fitness data remains keyed only by `userID`.

## Competition Data Model

The recommended competition model separates users from challenge membership.

User docs hold long-lived profile and target data:

```json
{
  "id": "user_jack",
  "type": "user",
  "userID": "Jack",
  "displayName": "Jack",
  "timezone": "Europe/London",
  "goalWeightKg": 87,
  "weeklyCalorieTarget": 16800,
  "active": true
}
```

Challenge docs define the competition:

```json
{
  "id": "challenge_2026_05_04",
  "type": "challenge",
  "challengeID": "challenge_2026_05_04",
  "name": "Weight Loss League",
  "status": "active",
  "startDate": "2026-05-10",
  "endDate": "2026-08-02",
  "timezone": "Europe/London",
  "weekStartsOn": "SUNDAY",
  "participants": ["Jack", "Ash"],
  "forfeits": {
    "weekly": {
      "enabled": true,
      "trigger": "lowest_weekly_score",
      "description": "Weekly loser posts a standard loss acknowledgement message in the family WhatsApp group.",
      "messageTemplate": "Weekly Challenge Update:\n\nI lost this week.\n\nNo excuses - I didn't meet the standard."
    },
    "monthly": {
      "enabled": true,
      "trigger": "lowest_monthly_total_score",
      "description": "Monthly loser wears the forfeit t-shirt at the next family event.",
      "forfeitItem": "Forfeit t-shirt",
      "minimumWearTimeMinutes": 60
    },
    "championship": {
      "enabled": true,
      "trigger": "lowest_challenge_total_score",
      "description": "Overall loser pays for dinner out for the winner and their partner, up to £75.",
      "spendLimitGBP": 75
    }
  },
  "scoringVersion": "v1"
}
```

For richer membership control, use `challenge_participant` docs:

```json
{
  "id": "challenge_2026_05_04__Jack",
  "type": "challenge_participant",
  "challengeID": "challenge_2026_05_04",
  "participantId": "challenge_2026_05_04__Jack",
  "userID": "Jack",
  "active": true,
  "joinedAt": "2026-05-09T00:09:40Z"
}
```

The scorer supports all of these participant sources for deployment safety:

- existing `type = "participant"` docs
- new `type = "challenge_participant"` docs
- `challenge.participants` arrays containing user IDs

If a matching `type = "user"` doc exists, the scorer enriches challenge participants with `displayName`, `timezone`, `goalWeightKg`, and `weeklyCalorieTarget`. Challenge participant fields can override user fields when needed.

The target values used in calculations are in:

```python
weekly_fitness_summary/constants.py
```

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

To send the weight summary locally:

```powershell
python -m weekly_fitness_summary.weekly_telegram_summary
```

To test FatSecret diary access:

```powershell
python -m weekly_fitness_summary.fatsecret
```

If `FATSECRET_ACCESS_TOKEN` and `FATSECRET_ACCESS_SECRET` are missing, `fatsecret.py` starts the FatSecret OAuth 1.0 authorization flow and prints the access token values to add to `.env`.

## Azure Function Schedule

The timer triggers are defined in `function_app.py`:

```python
weekly_fitness_summary: "0 30 7 * * 0"
sync_daily_fitness_raw: "0 45 23 * * *"
score_weekly_fitness_competition: "0 15 0 * * 0"
weekly_competition_leaderboard_summary: "0 30 7 * * 0"
monthly_competition_leaderboard_summary: "0 30 7 * * 0"
final_competition_leaderboard_summary: "0 30 7 * * 0"
```

Azure timer expressions use six fields:

```text
second minute hour day month day-of-week
```

The weekly summary and all three leaderboard timers run at `07:30 UTC` every Sunday, which is `08:30 BST` during the May-Aug UK daylight saving competition window. Raw sync runs daily at `23:45 UTC`. Competition scoring runs Sunday at `00:15 UTC` and scores the previous completed competition week.

The leaderboard timers are exclusive. They all wake up on Sunday, but only one sends a message:

- `final` wins if today is the last Sunday on or before the challenge `endDate`.
- `month` wins on the first Sunday after a month end.
- `week` runs on all other Sundays.

The older weight/food weekly summary also skips itself on monthly/final leaderboard Sundays so the competition message is not drowned out by routine weekly messages.

## Deployment

Deployment is handled by GitHub Actions:

```text
.github/workflows/main_fitness-weekly-update.yml
```

On push to `main`, the workflow:

1. Installs dependencies into `.python_packages/lib/site-packages`.
2. Zips the Azure Functions project.
3. Logs into Azure using GitHub Actions secrets.
4. Deploys to the `fitness-weekly-update` Function App.

Required Azure Function App settings include:

```text
FUNCTIONS_WORKER_RUNTIME=python
FUNCTIONS_EXTENSION_VERSION=~4
AzureWebJobsFeatureFlags=EnableWorkerIndexing
AzureWebJobsStorage=<storage connection string>
```

## Notes

- FatSecret food search uses OAuth 2.0.
- FatSecret personal diary access uses OAuth 1.0 delegated access.
- Food averages are calculated from logged days only. Days with no diary entries are excluded from the average.
- The calorie deficit/surplus compares total logged calories against the configured daily calorie target multiplied by the number of logged days.
- Competition scoring treats percentage bands as `minPct <= value < maxPct`. Keep scoring-rule percentage bands contiguous to avoid unscored gaps.
- If `calorieAdherence` has no `minDataPoints`, scoring defaults to a 5 logged-day guard and score documents include a warning. Add the guard to the rules explicitly to make the config self-documenting.
- The AI feedback prompt is competition-first. It reads the latest selected leaderboard type, matching `weekly_score` documents for that leaderboard period, the challenge, forfeits, scoring rules, participants, and compact raw fitness records. If no leaderboard exists yet, it falls back to the computed Renpho/FatSecret summaries and recent Cosmos DB records.

## Security

Secrets have previously been used during development. If any credential was ever committed or pasted into logs/chat, rotate it before relying on this automation.

Never commit:

- `.env`
- Telegram bot tokens
- Renpho credentials
- FatSecret client secrets, consumer secrets, or access tokens
- Cosmos DB connection strings
- Azure OpenAI keys

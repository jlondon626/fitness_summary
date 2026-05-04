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
RENPHO_JACK_EMAIL=your_jack_renpho_email
RENPHO_JACK_PASSWORD=your_jack_renpho_password
RENPHO_ASH_EMAIL=your_ash_renpho_email
RENPHO_ASH_PASSWORD=your_ash_renpho_password
FITNESS_SUMMARY_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id

FATSECRET_CLIENT_ID=your_fatsecret_oauth2_client_id
FATSECRET_CLIENT_SECRET=your_fatsecret_oauth2_client_secret
FATSECRET_JACK_CONSUMER_KEY=your_jack_fatsecret_oauth1_consumer_key
FATSECRET_JACK_CONSUMER_SECRET=your_jack_fatsecret_oauth1_consumer_secret
FATSECRET_JACK_ACCESS_TOKEN=your_jack_fatsecret_oauth1_access_token
FATSECRET_JACK_ACCESS_SECRET=your_jack_fatsecret_oauth1_access_secret
FATSECRET_ASH_CONSUMER_KEY=your_ash_fatsecret_oauth1_consumer_key
FATSECRET_ASH_CONSUMER_SECRET=your_ash_fatsecret_oauth1_consumer_secret
FATSECRET_ASH_ACCESS_TOKEN=your_ash_fatsecret_oauth1_access_token
FATSECRET_ASH_ACCESS_SECRET=your_ash_fatsecret_oauth1_access_secret

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

AZURE_OPENAI_API_KEY=your_azure_openai_key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=your_model_deployment_name
AZURE_OPENAI_API_VERSION=2024-02-15-preview
```

For Azure, add the same values as Function App application settings. Azure will not read your local `.env`.

`COSMOS_DB_AI_QUERY` is optional. Set it if your container needs a more specific query for the records you want the AI to see.

`COSMOS_DB_RAW_CONTAINER_NAME` defaults to `fitness_raw`. The daily raw sync writes one Renpho document and one FatSecret document per user/date using deterministic ids such as `renpho__user_jack__2026-05-04` and `fatsecret__user_jack__2026-05-04`. Users to sync are read from `fitness_competitions` user documents with enabled Renpho/FatSecret sources, not from app settings.

`COSMOS_DB_HEALTH_CONTAINER_NAME` defaults to the raw container. If the Apple Health API writes to a separate container, set this to that container name. If it uses a separate Cosmos account or database, set `COSMOS_DB_HEALTH_CONNECTION_STRING` and `COSMOS_DB_HEALTH_DATABASE_NAME`. The scorer also understands the Apple Health API names `COSMOS_ENDPOINT`, `COSMOS_KEY`, `COSMOS_DATABASE`, and `COSMOS_CONTAINER`. Competition scoring reads `type = "apple-health-data"` rows and maps `active_energy_kcal` into the active-calorie score.

`COSMOS_DB_COMPETITIONS_CONTAINER_NAME` defaults to `fitness_competitions`. Weekly scoring reads all active challenges, participants, users, forfeits, and scoring rules from this container, then writes `weekly_score` and leaderboard documents back into it.

Challenge selection, user identity, and leaderboard kind are database/application data rather than secrets. The scheduled jobs read active challenge documents from Cosmos. Weekly/monthly/final timers pass the leaderboard kind directly.

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
  "averageDailyCalorieTarget": 2400,
  "syncSources": {
    "renpho": {
      "enabled": true,
      "credentialRef": "jack"
    },
    "fatsecret": {
      "enabled": true,
      "credentialRef": "jack"
    },
    "appleHealth": {
      "enabled": true
    }
  },
  "active": true
}
```

Use the same shape for Ash with `credentialRef: "ash"` once his source credentials are available. If Ash should only use Apple Health for now, keep Renpho/FatSecret disabled:

```json
{
  "id": "user_ash",
  "type": "user",
  "userID": "Ash",
  "displayName": "Ash",
  "timezone": "Europe/London",
  "goalWeightKg": 90,
  "averageDailyCalorieTarget": 2300,
  "syncSources": {
    "renpho": {
      "enabled": false,
      "credentialRef": "ash"
    },
    "fatsecret": {
      "enabled": false,
      "credentialRef": "ash"
    },
    "appleHealth": {
      "enabled": true
    }
  },
  "active": true
}
```

`credentialRef` maps to Function App setting names by uppercasing the ref. For example, `credentialRef: "ash"` maps to `RENPHO_ASH_EMAIL`, `RENPHO_ASH_PASSWORD`, `FATSECRET_ASH_ACCESS_TOKEN`, and related FatSecret settings. The older generic Jack settings `MY_EMAIL`, `MY_PASSWORD`, `FATSECRET_ACCESS_TOKEN`, and `FATSECRET_ACCESS_SECRET` still work as fallback for Jack only, but the per-user names above are preferred.

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

If a matching `type = "user"` doc exists, the scorer enriches challenge participants with `displayName`, `timezone`, `goalWeightKg`, and `averageDailyCalorieTarget`. Challenge participant fields can override user fields when needed. `weeklyCalorieTarget` is still accepted as a backwards-compatible fallback for older user documents.

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

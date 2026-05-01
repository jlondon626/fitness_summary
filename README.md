# Fitness Summary

Python automation for sending a weekly fitness and nutrition summary to Telegram.

The project pulls:

- Renpho scale measurements for rolling weekly weight and body-fat averages.
- FatSecret food diary entries for calorie and protein averages.
- Cosmos DB fitness records for AI context.
- Azure OpenAI for weekly coaching feedback.
- Telegram bot credentials for posting the summary message.

The production entry point is an Azure Functions timer trigger in `function_app.py`.

## What It Sends

The Azure timer sends three Telegram messages:

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

- AI coaching feedback:
  - Feedback on the previous week.
  - Advice for the week ahead.
  - One measurable focus.

## Project Structure

```text
function_app.py                         Azure Functions timer entry point
host.json                               Azure Functions host config
requirements.txt                        Python dependencies
weekly_fitness_summary/
  constants.py                          Weight and nutrition targets
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
COSMOS_DB_AI_ITEM_LIMIT=100
COSMOS_DB_AI_QUERY=SELECT TOP 100 * FROM c ORDER BY c._ts DESC

AZURE_OPENAI_API_KEY=your_azure_openai_key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=your_model_deployment_name
AZURE_OPENAI_API_VERSION=2024-02-15-preview
```

For Azure, add the same values as Function App application settings. Azure will not read your local `.env`.

`COSMOS_DB_AI_QUERY` is optional. Set it if your container needs a more specific query for the records you want the AI to see.

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

The timer trigger is defined in `function_app.py`:

```python
schedule="0 30 7 * * 0"
```

Azure timer expressions use six fields:

```text
second minute hour day month day-of-week
```

This schedule runs at `07:30 UTC` every Sunday, which is `08:30 BST` during UK daylight saving time.

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
- The AI feedback prompt includes the computed Renpho/FatSecret summaries and recent Cosmos DB records. Keep the Cosmos query scoped to useful fitness data so prompts stay small and relevant.

## Security

Secrets have previously been used during development. If any credential was ever committed or pasted into logs/chat, rotate it before relying on this automation.

Never commit:

- `.env`
- Telegram bot tokens
- Renpho credentials
- FatSecret client secrets, consumer secrets, or access tokens
- Cosmos DB connection strings
- Azure OpenAI keys

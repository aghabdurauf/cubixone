# CubixOne

**Reports, dashboards and AI, end to end.** By Cubix Labs.

CubixOne is a self-hosted reporting portal. People sign in and open the reports
and dashboards shared with them. Administrators connect databases, upload or
register items, and decide who sees what. Developers describe a report or
dashboard in plain words and an AI agent builds it from live data. An Assistant
answers questions from the items each person is allowed to open.

It runs entirely in Docker: one app container, plus an optional MySQL container
for user accounts.

## Install

You need Docker with Compose v2.

```bash
git clone https://github.com/<your-org>/cubixone.git
cd cubixone
./install.sh
```

The installer asks two things:

1. **The port** to serve on (default 8080).
2. **Where user accounts live:** the **built-in MySQL** (started with CubixOne;
   nothing else to install) or **your own database** (PostgreSQL, SQL Server or
   MySQL). CubixOne creates its tables, all named `cx_...`, on first start.

Then open `http://<server>:8080`, create the first administrator, and you're in.
`./install.sh -y` installs without questions (built-in MySQL, port 8080).

Upgrade: `git pull && docker compose up -d --build`. Logs: `docker compose logs -f app`.

## What's inside

| Area | What it does |
|---|---|
| **Reports and Dashboards** | The two sections, each with its own tree of subfolders. Items are an uploaded `.html` / `.zip`, a registered live app, or an item built by Developer mode. |
| **Access** | Roles: *admin*, *developer*, *viewer*. Folders and items are *Everyone* or *Restricted* to named people; a restricted folder closes everything under it. Items you can't open return "not found". |
| **Data connections** | SQL Server, Oracle, PostgreSQL, Supabase, pgvector, Amazon Redshift, MySQL, MariaDB, SQLite. Add them in the portal, **Test connection**, browse tables, views, procedures and columns. Snowflake, BigQuery, Databricks, ClickHouse, MongoDB and REST are planned. |
| **LLM & Bot** | Connect your own model with your own API key: Anthropic Claude, OpenAI, Azure OpenAI, Google Gemini, Mistral, Ollama, or any OpenAI-compatible endpoint. Keys are encrypted and used only by the server. |
| **Assistant** | Answers from the reports and dashboards the person asking may open, and names its sources. |
| **Developer mode** | Tick tables / views / procedures from one or several connections, describe what you need, choose Report or Dashboard, check the preview, pick a folder, publish. The agent writes read-only SQL per database, joins results in the page, and the published item re-runs its queries on open, hourly or daily. |
| **Skills** | In a Developer-mode prompt, write `use <name> skill from <path>`. The agent reads that skill (a `SKILL.md` or `.md` file in the `skills/` folder, mounted at `/app/skills`) and follows it. Skills are never shown in the chat or the portal. |
| **Audit log** | Sign-ins, views, refusals, uploads, publishes, connection and model tests, Assistant questions, with user and IP. Export to CSV. |

## Security

- Passwords are hashed with scrypt; accounts lock after repeated failures; new
  and reset passwords must be changed at first sign-in.
- Uploaded pages run in a sandboxed frame without same-origin access, so their
  scripts can't read the portal's cookies or pages. Their assets load with a
  short-lived ticket in the URL.
- AI-built pages are self-contained and served with a policy that blocks all
  outside network access.
- Developer mode runs only single `SELECT` statements and opens PostgreSQL
  sessions read-only. **Still give CubixOne read-only database logins**: that is
  the real protection.
- Database passwords and API keys are encrypted with a key derived from
  `SECRET_KEY`. Keep `.env` private, back it up, and never change `SECRET_KEY`.
- Serve it over HTTPS behind a reverse proxy or load balancer, and set
  `COOKIE_SECURE=true`.

## Backups

Back up the user-store database (the `mysql-data` volume or your own database),
the `cubixone-data` volume (uploaded and AI-built pages), and `.env`.

## Try it in GitHub Codespaces

Open the repository in a Codespace (*Code → Codespaces → Create*). Docker is
already there. Run `./install.sh -y`, then open the forwarded port 8080.

## Project layout

```
app/main.py         web API, viewer, permissions
app/db.py           CubixOne's own tables
app/security.py     passwords, encryption, access rules
app/connectors.py   data connections, schema, read-only queries
app/llm.py          AI providers
app/agent.py        Assistant and Developer mode
app/storage.py      uploaded files
app/static/         the portal (HTML, CSS, JS; no build step)
install.sh          installer
docker-compose.yml  app + optional MySQL
```

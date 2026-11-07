"""
AI Service — proxies to OpenAI-compatible endpoint or uses FallbackChatbot.

Context window cap: last 20 messages are sent to the AI to keep context
manageable. Full history is stored in DB and rendered in the UI.
"""

import json
import re
import requests
from flask import current_app
from services.exceptions import AIServiceUnavailableError


CATEGORIES = [
    "login",
    "password",
    "crashes",
    "api_errors",
    "database",
    "installation",
    "permissions",
    "web_bugs",
    "general",
]

CATEGORY_LABELS = {
    "login": "Login and Authentication",
    "password": "Password and Account Access",
    "crashes": "Application Crashes and Errors",
    "api_errors": "API and Server Errors",
    "database": "Database and SQL Issues",
    "installation": "Software Installation and Updates",
    "permissions": "Application Permissions",
    "web_bugs": "Web Application Bugs",
    "general": "General Software Support",
}

SYSTEM_PROMPT = """You are ResolveAI, an expert AI support assistant for software and IT issues.
Your job is to help employees troubleshoot software problems step by step.

Software issue categories: Login/Authentication, Password/Account Access,
Application Crashes, API/Server Errors, Database/SQL, Software Installation,
Permissions, Web Application Bugs, General Software Support.

Rules:
1. Understand the problem in natural language.
2. Ask clarifying questions when needed.
3. Classify the issue and estimate priority (Low/Normal/High/Urgent).
4. Give 3-5 concrete troubleshooting steps when you can identify useful actions.
5. End a useful troubleshooting response with
   <confirm>Is your issue resolved?</confirm>.
6. Do not claim the issue is resolved. The application waits for the employee's
   explicit confirmation before marking the ticket resolved.
7. Escalate persistent or high-risk issues.

After your message, append a JSON metadata block:
<metadata>{"category": "login", "priority": "Normal", "suggested_ticket": false}</metadata>

Use only software categories — do NOT suggest hardware repairs.
"""


class OpenAIAdapter:

    @staticmethod
    def respond(conversation_history: list, user_message: str, kb_context: list) -> dict:
        api_key = current_app.config.get("OPENAI_API_KEY", "")
        api_base = current_app.config.get("OPENAI_API_BASE", "https://api.openai.com/v1")
        model = current_app.config.get("OPENAI_MODEL", "gpt-4o-mini")

        kb_text = ""
        if kb_context:
            kb_text = "\n\nRelevant Knowledge Base Articles:\n" + "\n".join(
                f"- {a['title']}: {a['solution'][:200]}" for a in kb_context
            )

        messages = [{"role": "system", "content": SYSTEM_PROMPT + kb_text}]

        # Cap at last 20 messages for context window management
        recent_history = conversation_history[-20:] if len(conversation_history) > 20 else conversation_history
        for msg in recent_history:
            if msg.get("role") in ("user", "assistant"):
                messages.append({"role": msg["role"], "content": msg["content"]})

        messages.append({"role": "user", "content": user_message})

        try:
            response = requests.post(
                f"{api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": model, "messages": messages, "max_tokens": 1024},
                timeout=30,
            )
        except requests.Timeout:
            raise AIServiceUnavailableError("AI service timed out after 30 seconds.")
        except requests.RequestException as exc:
            raise AIServiceUnavailableError(f"AI service request failed: {exc}")

        if response.status_code != 200:
            raise AIServiceUnavailableError(
                f"AI service returned HTTP {response.status_code}"
            )

        try:
            data = response.json()
            raw_content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIServiceUnavailableError(
                "AI service returned an invalid response."
            ) from exc

        if not isinstance(raw_content, str) or not raw_content.strip():
            raise AIServiceUnavailableError(
                "AI service returned an empty response."
            )

        # Extract <confirm> tag
        ask_confirmation = False
        if "<confirm>" in raw_content:
            ask_confirmation = True
            raw_content = re.sub(r"<confirm>.*?</confirm>", "", raw_content, flags=re.DOTALL).strip()

        # Extract <metadata> block
        category = "general"
        priority = "Normal"
        suggested_ticket = False
        meta_match = re.search(r"<metadata>(.*?)</metadata>", raw_content, re.DOTALL)
        if meta_match:
            raw_content = raw_content[: meta_match.start()].strip()
            try:
                meta = json.loads(meta_match.group(1))
                category = meta.get("category", "general")
                priority = meta.get("priority", "Normal")
                suggested_ticket = bool(meta.get("suggested_ticket", False))
            except (json.JSONDecodeError, KeyError):
                pass

        return {
            "role": "assistant",
            "content": raw_content,
            "category": category,
            "priority": priority,
            "suggested_ticket": suggested_ticket,
            "ask_confirmation": ask_confirmation,
        }


class FallbackChatbot:
    """
    Rules-based chatbot for when no OpenAI API key is configured.
    Covers all 9 software support categories with multi-step troubleshooting.
    Sets ask_confirmation=True after providing troubleshooting steps.
    """

    PATTERNS = {
        "login": {
            "keywords": [
                "login", "log in", "sign in", "signin", "can't access", "cannot access",
                "access denied", "authentication", "credentials", "username", "sso",
                "single sign", "ldap", "active directory",
            ],
            "priority": "High",
            "steps": [
                "Verify you're using the correct email address and password.",
                "Check if Caps Lock is on — passwords are case-sensitive.",
                "Clear your browser cookies and cache, then try again.",
                "Try opening the login page in a private/incognito browser window.",
                "If using SSO or Active Directory, ensure you're connected to the corporate network or VPN.",
                "If the issue persists, use the 'Forgot Password' link to reset your credentials.",
            ],
        },
        "password": {
            "keywords": [
                "password", "forgot password", "reset password", "change password",
                "expired password", "account locked", "locked out", "unlock account",
                "2fa", "two factor", "mfa", "multi-factor",
            ],
            "priority": "High",
            "steps": [
                "Click 'Forgot Password' on the login page and follow the reset email link.",
                "Check your spam/junk folder if you don't receive the reset email within 5 minutes.",
                "Ensure your new password meets the complexity requirements (min 8 chars, uppercase, number).",
                "If your account is locked, wait 15 minutes or contact IT support to unlock it.",
                "For MFA issues, use your backup codes or contact IT to reset your authenticator.",
                "After resetting, clear browser cache and try logging in again.",
            ],
        },
        "crashes": {
            "keywords": [
                "crash", "crashes", "crashing", "not responding", "freezing", "freeze",
                "hung", "hang", "hangs", "error message", "runtime error", "exception",
                "application stopped", "app stopped", "not opening", "won't open",
                "white screen", "black screen", "loading forever",
            ],
            "priority": "High",
            "steps": [
                "Note the exact error message or code shown before the crash.",
                "Close the application completely and reopen it.",
                "Check if the issue happens with a specific file or action — try reproducing it.",
                "Clear the application's cache: go to Settings > Clear Cache (or equivalent).",
                "Update the application to the latest version from the software portal.",
                "If the crash persists, check Windows Event Viewer / macOS Console for crash logs.",
                "Try running the application as Administrator (right-click > Run as Administrator).",
            ],
        },
        "api_errors": {
            "keywords": [
                "api", "500", "503", "502", "504", "server error", "internal server",
                "service unavailable", "gateway", "timeout", "connection refused",
                "endpoint", "rest", "http error", "status code", "bad gateway",
            ],
            "priority": "Urgent",
            "steps": [
                "Check the exact HTTP status code: 500 = server error, 503 = service unavailable, 504 = gateway timeout.",
                "Verify the service status page or dashboard for any known outages.",
                "Check your network connectivity and ensure you can reach the server.",
                "Review the API request — ensure the endpoint URL, headers, and payload are correct.",
                "Check the application logs for detailed error stack traces.",
                "If this is a third-party API, check their status page (e.g., statuspage.io).",
                "Try the request again after 5 minutes — transient errors often self-resolve.",
            ],
        },
        "database": {
            "keywords": [
                "database", "sql", "db", "query", "connection pool", "db error",
                "cannot connect to database", "database connection", "timeout query",
                "deadlock", "transaction", "migration", "schema", "postgres", "mysql",
                "sqlite", "oracle", "mssql",
            ],
            "priority": "Urgent",
            "steps": [
                "Check if the database service is running: look in Services / Task Manager.",
                "Verify the database connection string in the application configuration.",
                "Test connectivity: try pinging the database host or using a DB client tool.",
                "Check if the connection pool is exhausted — review max connections settings.",
                "Look at database server logs for specific error messages.",
                "For slow queries, check for missing indexes or long-running transactions.",
                "Ensure firewall rules allow traffic on the database port (default 5432/3306/1433).",
            ],
        },
        "installation": {
            "keywords": [
                "install", "installation", "update", "upgrade", "download", "setup",
                "uninstall", "reinstall", "version", "software update", "patch",
                "deployment", "package", "dependencies", "npm", "pip", "msi",
            ],
            "priority": "Normal",
            "steps": [
                "Run the installer as Administrator (right-click > Run as Administrator).",
                "Temporarily disable antivirus software during installation.",
                "Ensure you have sufficient disk space (check drive properties).",
                "Check that your OS version meets the minimum requirements.",
                "Download a fresh copy of the installer — the existing file may be corrupted.",
                "Check Windows Update for any pending OS updates that might be required.",
                "For update failures, try uninstalling the current version first, then reinstalling.",
            ],
        },
        "permissions": {
            "keywords": [
                "permission", "permissions", "access denied", "unauthorized", "forbidden",
                "403", "privilege", "privileges", "admin rights", "read only",
                "cannot save", "cannot delete", "restricted", "role", "group policy",
            ],
            "priority": "Normal",
            "steps": [
                "Verify your user role and permissions in the application settings.",
                "Contact your manager or IT admin to request the required access level.",
                "Check if Group Policy is restricting the action (common in corporate environments).",
                "Try logging out and back in to refresh your permission tokens/session.",
                "For file system permissions, right-click the file/folder > Properties > Security.",
                "Check if the feature requires a specific license tier or subscription level.",
            ],
        },
        "web_bugs": {
            "keywords": [
                "website", "web app", "browser", "page not loading", "blank page",
                "button not working", "form not submitting", "javascript", "css",
                "layout broken", "display issue", "ui bug", "frontend", "rendering",
                "cors", "cookie", "session expired", "404 not found",
            ],
            "priority": "Normal",
            "steps": [
                "Hard-refresh the page: Ctrl+F5 (Windows) or Cmd+Shift+R (Mac).",
                "Clear browser cache and cookies, then reload.",
                "Try a different browser (Chrome, Firefox, Edge) to isolate the issue.",
                "Open Developer Tools (F12) > Console tab and note any error messages.",
                "Disable browser extensions one by one — an extension may be interfering.",
                "Check if the issue occurs in private/incognito mode (no extensions active).",
                "If a form isn't submitting, check that all required fields are filled correctly.",
            ],
        },
        "general": {
            "keywords": [],
            "priority": "Normal",
            "steps": [
                "Describe the exact steps to reproduce the issue.",
                "Note any error messages, codes, or unusual behavior.",
                "Try restarting the application and your computer.",
                "Check if other users in your team are experiencing the same issue.",
                "Review recent changes — did this start after a software update or configuration change?",
                "Check the application's help documentation or FAQ section.",
                "If the problem persists, I'll create a support ticket for the IT team.",
            ],
        },
    }

    @staticmethod
    def respond(conversation_history: list, user_message: str, kb_context: list) -> dict:
        msg_lower = user_message.lower()

        # Combine recent history for context
        recent_text = " ".join(
            m.get("content", "").lower()
            for m in conversation_history[-3:]
            if m.get("role") == "user"
        )
        combined = msg_lower + " " + recent_text

        # Score each category by keyword hits
        scores = {}
        for cat, data in FallbackChatbot.PATTERNS.items():
            if cat == "general":
                continue
            score = sum(1 for kw in data["keywords"] if kw in combined)
            if score > 0:
                scores[cat] = score

        best_cat = max(scores, key=scores.get) if scores else "general"
        pattern = FallbackChatbot.PATTERNS[best_cat]

        # Build response
        cat_label = CATEGORY_LABELS.get(best_cat, "General Software Support")
        steps_text = "\n".join(
            f"{i+1}. {step}" for i, step in enumerate(pattern["steps"])
        )

        kb_section = ""
        if kb_context:
            kb_section = "\n\n**Related Knowledge Base Articles:**\n" + "\n".join(
                f"- {a['title']}" for a in kb_context
            )

        history_length = len(conversation_history)
        suggested_ticket = history_length > 4

        content = (
            f"I can see this is a **{cat_label}** issue. "
            f"Here are the troubleshooting steps to resolve it:\n\n"
            f"{steps_text}"
            f"{kb_section}"
        )

        if suggested_ticket:
            content += "\n\n> 💡 Since we've been troubleshooting for a while, would you like me to create a support ticket for the IT team?"

        return {
            "role": "assistant",
            "content": content,
            "category": best_cat,
            "priority": pattern["priority"],
            "suggested_ticket": suggested_ticket,
            "ask_confirmation": True,  # Always ask after providing steps
        }


class AIService:

    @staticmethod
    def get_response(
        conversation_history: list,
        user_message: str,
        kb_context: list,
    ) -> dict:
        """
        Route to OpenAIAdapter if API key is configured, otherwise FallbackChatbot.
        Returns dict with keys: role, content, category, priority, suggested_ticket, ask_confirmation.
        """
        api_key = current_app.config.get("OPENAI_API_KEY", "")
        if not api_key:
            return FallbackChatbot.respond(conversation_history, user_message, kb_context)
        return OpenAIAdapter.respond(conversation_history, user_message, kb_context)

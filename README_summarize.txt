GLAM Gemini MVP is an inventory management system that integrates Telegram voice commands with Gemini AI. It transcribes voice messages into structured inventory actions (add/sell items, tracking units like pieces or meters) for database storage (SQLite locally, PostgreSQL on Vercel).

Key features include:
*   **Voice Processing:** Gemini-powered transcription, automatic product name matching, and custom voice corrections for commands.
*   **Comprehensive Inventory:** Adding, selling, listing, editing, and archiving items, with historical pricing and profit calculation.
*   **Web Panel:** A web interface for detailed sales analytics (by date), inventory management, and user administration.
*   **Multi-Branch & User System (New):** Supports multiple stores (e.g., main store for bulk, specific branches for unique item IDs). It introduces a role-based user system (Admin, Seller) with login/password, allowing sellers to manage their store's inventory and view daily sales.
*   **Financial Tracking (New):** A daily settlement feature for sellers, recording debts or advances based on sales remittances.

Setup requires Python 3.11+, dependencies, and `.env` configuration for Telegram and Gemini API keys and the admin ID. It can run locally or be deployed to Vercel via webhooks.
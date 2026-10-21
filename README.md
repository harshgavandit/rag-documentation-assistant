# Docwise

Docwise is a web app for asking questions about MCP and AI agent documentation. It searches the included documents and uses the relevant passages to generate an answer with source citations.

[Open the app](https://rag-documentation-assistant.vercel.app/)

## What it does

- Accepts a question or lets you choose one of the example questions.
- Searches the documentation using keyword and semantic search.
- Shows an answer with the document name and section cited.
- Lets you expand the retrieved passages and read the original text.
- Provides a copy button for the answer.
- Returns a refusal when it cannot answer from the documentation.
- Limits requests through Redis and shows a retry message when the limit is reached.

## Included documentation

The current app uses two Markdown guides:

- **Building a Custom MCP Server:** FastMCP tool registration, server transports, database tools, and server setup.
- **AI Agents with Google Maps:** Traditional function tools, MCP connections, tool discovery, and architecture comparisons.

You can ask questions such as:

- How does FastMCP register a tool?
- What is the difference between Stdio and Streamable HTTP?
- How does MCP differ from traditional function tools?

The web app searches this bundled library; it does not have a document upload feature. Questions and retrieved passages are sent to OpenAI to generate answers. The application does not save question history.

The repository also includes a command-line version for indexing Markdown files, asking questions, and comparing character-based splitting with Markdown heading-based splitting.

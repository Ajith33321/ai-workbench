# Interview notes — say this in simple English

Read this out loud once or twice before any AI-engineer call. If you cannot explain a line, do not claim it on the resume. This project is real: login, a database, public links, and an LLM step. It is a portfolio app, not a live product with paying users. Never invent metrics (no “1,000 meetings processed”, no “92% accuracy”).

---

## One-minute story

I am moving from performance marketing into junior AI engineering. I built two small web apps so I could show the work, not just talk about ideas.

**Book & Record** is a meeting assistant. I can create a booking link, someone books a slot, then I paste the transcript or upload audio. The app stores that in a database, sends the text to a language model with a strict JSON prompt, and comes back with a summary, checkpoints, decisions, and a to-do list with owners.

**Review Rewards** is for a restaurant group. Each branch has a public link and a QR code. A guest writes a review. The app scores sentiment, tags food, service and wait, and can issue a coupon or a small cashback code. The owner dashboard reads those rows from the database — it is not a fake screenshot.

If there is no OpenAI key, the same buttons still work, in a demo mode I wrote, so anyone can try the path end to end.

---

## The stack, in plain words

- **Python + FastAPI.** FastAPI is a Python web framework. Each URL is a function. Some functions return a web page. Some return JSON for tests and for the API.
- **Jinja templates.** The HTML pages. The server fills in names, tables and scores before sending the page.
- **SQLite + SQLAlchemy.** SQLite is a database in a file. SQLAlchemy is how the Python code talks to it. Meetings, reviews and coupons are rows in tables. When I open the dashboard, the app queries those tables.
- **JWT auth.** After login, the server gives me a signed token. The browser stores it in an httpOnly cookie. On the next request the server checks the signature and expiry, then loads my user. That is how it knows whose meetings to show. The API can also send the same token as `Authorization: Bearer …`.
- **Password hashing.** The password is not stored as text. It is passed through PBKDF2 (a slow hash with a salt) so a stolen database is not enough to read the password.
- **OpenAI API.** With a key: Whisper turns audio into text, and a chat model (default `gpt-4o-mini`) returns JSON for notes or for sentiment. Without a key: demo mode uses rules I wrote, so the app still runs.
- **QR codes.** Each branch token is turned into a PNG that points at `/r/<token>`.
- **Render / Docker.** The repo has a Dockerfile, a Procfile and render.yaml so it can be deployed with one service on Render’s free plan.

I am a beginner in Python professionally (no years as a Python engineer). I can walk through this codebase: routes, the data model, the prompts, and the auth flow.

---

## JWT, a little deeper

JWT means JSON Web Token. It has three parts: a header, a payload, and a signature, all Base64-encoded and joined by dots.

The payload I put in is: user id, email, role, issued-at, expiry. The signature is HMAC-SHA256 with a secret (`JWT_SECRET`). Anyone can decode the payload, but they cannot change it without the secret, because the signature would no longer match.

Why JWT and not only a server session? A JWT is self-contained. The API and the web pages can use the same token. For a small app a server session would also have been fine. I would say that honestly if asked.

Expiry matters. When the token is old, the server rejects it and the person signs in again.

---

## Database queries, in plain words

Examples of what the app actually does:

- “Give me all meetings where `host_id` is the signed-in user, newest first.”
- “Find the booking slot with this public token.” (The token is in the URL, not the user id, so a guest can book without an account.)
- “Insert a review, then insert a coupon row that points at that review.”
- On the owner dashboard: group reviews by branch, average the sentiment score, count positive / negative, count coupons.

I would not call this “big data” or “RAG”. There is no vector database here. Search is ordinary SQL filters.

If this product grew, I would move SQLite to Postgres, add indexes on `host_id`, `branch_id` and the public tokens, and put the app behind HTTPS on Render or similar.

---

## Prompt design, in plain words

A prompt is the instruction I give the model. I do not just say “summarise this.” I tell it:

- the job (“meeting-notes assistant” or “score this restaurant review”),
- the exact JSON shape I want back,
- the rules: do not invent facts, use “unspecified” when the owner is missing, do not reward empty reviews, do not raise a score because someone asked for a discount.

I also ask the API for `response_format: json_object` so I get JSON I can parse and save. If the model call fails, I fall back to demo logic so the user is not stuck.

This is called **structured output** or a **constrained prompt**. The benefit is I can store fields in the database (owner, score, theme) instead of a blob of prose.

What can go wrong? The model can still invent a to-do or misread a joke as anger. So the host should glance at the notes, and the owner should treat sentiment as a signal, not as a final verdict. I would not auto-pay cashback without a human rule or a cap.

---

## Demo mode vs live mode

- No `OPENAI_API_KEY`: the app never calls OpenAI. Audio gets a clearly labelled stand-in transcript. Summaries are built from sentences and keywords. Sentiment uses positive/negative word lists and simple theme dictionaries. This is deterministic: the same review gets the same score.
- With a key: Whisper transcribes the file; the chat model uses the prompts above.

I will always say which mode I am in. I will not pretend a demo-mode score is a trained machine-learning model.

---

## How I would scale this (honest junior answer)

- **Users:** SQLite is fine for a demo. For many restaurants I would use Postgres and a login provider (or still JWT, with refresh tokens).
- **Audio:** Whisper has a file-size limit (I cap uploads at 25 MB). Longer meetings would be chunked, or I would use a queue (the web request should not wait on a ten-minute file).
- **Cost:** I would log tokens and prefer a small model (`gpt-4o-mini` or equal). I would not send the same transcript twice if the notes already exist.
- **Safety:** Coupons should have a cap and an expiry. Do not trust the model to decide money. Reviews may contain phone numbers — do not log them to a public place.
- **Search across old meetings:** that is where a vector store / RAG could come later. It is not in this project. If asked, I would say: “I would chunk transcripts, store embeddings, and retrieve the few closest chunks before asking the model. I have not built that here.”

---

## Likely questions, and straight answers

**“Did you really build this?”**  
Yes, this is my portfolio project. I can open the repo, run it, and walk through `app/main.py`, `app/models.py`, `app/auth.py` and `app/llm.py`. I should be able to change a prompt or add a field if asked.

**“What is the difference between this and ChatGPT?”**  
ChatGPT is a chat box. This is an application: accounts, a database, public links, stored outputs, and a dashboard that reads those outputs later.

**“Why not LangChain?”**  
I did not need it. I have two prompts and two API calls. A framework would have hidden the part I wanted to learn. I can add LangChain later if I build retrieval or tool-calling agents.

**“Is this RAG?”**  
No. RAG means retrieve relevant documents, then generate an answer from them. This app sends the transcript or review you just gave it. I will not write “RAG” on the resume for this project.

**“How do you authenticate the API?”**  
JWT, signed with a secret, sent as a cookie or a Bearer token. Protected routes depend on `get_current_user`.

**“What is in the JWT?”**  
User id, email, role, times. Not the password.

**“How do you stop one user seeing another user’s meetings?”**  
Every query filters on the signed-in user’s id. Knowing a meeting number is not enough; it also has to belong to you.

**“What if someone guesses a booking token?”**  
Tokens are random URL-safe strings. They are not secret passwords, but they are hard to guess. For a real product I would add expiry and maybe a short PIN.

**“How do you test it?”**  
`python tests/smoke_test.py` signs in, creates a slot, books it, processes a transcript, checks the notes came back from the database, submits a review, checks sentiment and the coupon row, and checks that a new account cannot see someone else’s data.

**“What would you do next?”**  
Deploy it on Render, put the live link on my resume, add a “host edits the notes before they are final” step, and maybe a simple weekly email of branch scores. I would not jump to a vector database until someone actually needs to search old meetings in natural language.

**“You come from marketing. Why AI engineering?”**  
I already use ChatGPT, Claude and Gemini every day on campaigns. I wanted to understand the application side: auth, data, prompts, and a deployable service. These two ideas — meeting notes and review-for-rewards — came from problems I actually see in marketing and local business work.

**“What is your Python level?”**  
Beginner professionally. Zero years as a professional Python engineer. I can read and change this project. I am honest about that.

---

## Demo path to click in an interview (2–3 minutes)

1. Open the home page. Point at demo mode if there is no key.
2. Sign in as `host@demo.local` / `demo1234`. Show the meetings desk and the already processed “Q4 campaign review”. Open it: summary, checkpoints, to-dos with Priya, Ravi, Ajith.
3. Open the public booking link in a new tab (no login).
4. Sign out, sign in as `owner@demo.local` / `demo1234`. Show Spice Garden, branch scores, the QR, the review table, the coupon codes.
5. Optionally submit a new review on the Koramangala link and watch a new row and code appear.

If the interviewer wants code, start with `app/llm.py` (prompts) and `app/auth.py` (JWT), then the process-meeting and submit-review functions in `app/main.py`.

"""Inspector portal: the developers' way in. A FastAPI backend (uploads, jobs, reports) and a Streamlit UI.

The portal never validates anything itself: it stores packages where the agents can read them, asks the
Inspector over A2A, and keeps the results.
"""

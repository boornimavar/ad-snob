# Creative Lens analysis service + team gallery, for a Hugging Face Docker Space.
# Settings come from the Space's secrets: CREATIVE_LENS_DATABASE_URL,
# SUPABASE_URL, SUPABASE_SECRET_KEY, CREATIVE_LENS_API_KEY.
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr git unzip \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face runs the container as user 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --user --upgrade pip \
    && pip install --user -r requirements.txt \
    && pip install --user torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip install --user git+https://github.com/openai/CLIP.git
# Download CLIP's weights now, not on the first search.
RUN python -c "import clip; clip.load('ViT-B/32', device='cpu')"

# meta_ads_collector 1.4.0 from the zip, never from pip (see CLAUDE.md).
COPY --chown=user meta_ads_collector_source.zip /tmp/
RUN mkdir -p meta_ads_collector && unzip -q /tmp/meta_ads_collector_source.zip -d meta_ads_collector \
    && rm /tmp/meta_ads_collector_source.zip

COPY --chown=user analysis_api.py library.py meta_collection.py meta_design_scraper_session.py gallery.html \
    design_classifier.pkl rank_model_v1.pkl ./
RUN python -m py_compile analysis_api.py library.py meta_collection.py meta_design_scraper_session.py

EXPOSE 7860
# One worker only: a search's packets are held in that worker's memory.
CMD ["gunicorn", "--bind", "0.0.0.0:7860", "--workers", "1", "--threads", "4", "--timeout", "900", "analysis_api:app"]

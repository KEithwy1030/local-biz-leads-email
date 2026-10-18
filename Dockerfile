# Apify Actor image for local-biz-leads-email
FROM apify/actor-python:3.13

COPY --chown=myuser:myuser requirements.txt ./

RUN echo "Python version:" \
 && python --version \
 && echo "Pip version:" \
 && pip --version \
 && echo "Installing dependencies:" \
 && pip install -r requirements.txt \
 && echo "All installed Python packages:" \
 && pip freeze

COPY --chown=myuser:myuser . ./

RUN python -m compileall -q my_actor/

CMD ["python", "-m", "my_actor"]

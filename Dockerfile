FROM docker.io/python:3-alpine as base

# Builder Container to install psycopg on Alpine
FROM base as builder
RUN mkdir /install \
    && apk update  \
    && apk add build-base postgresql-dev python3-dev musl-dev libffi-dev openssl-dev
WORKDIR /install
COPY requirements.txt /requirements.txt
RUN pip install --prefix=/install -r /requirements.txt

# Actual image.
FROM base
COPY --from=builder /install /usr/local
# psycopg needs libpq
RUN apk --no-cache add libpq alpine-conf
RUN setup-timezone -z America/New_York
COPY . /app/
WORKDIR /app
CMD /app/run.sh

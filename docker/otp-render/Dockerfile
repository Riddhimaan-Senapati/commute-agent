# OpenTripPlanner for the hosted demo, run as a Render private service next to the web app.
# The saved graph (data/otp/graph.obj, about 112 MB) is a release asset, not in git.
# This folder is the build context, so the Render service sets it as its root directory.

FROM curlimages/curl:8.11.1 AS graph
ARG GRAPH_URL=https://github.com/Riddhimaan-Senapati/commute-agent/releases/download/data-v1/otp-graph.obj
RUN curl -fsSL -o /tmp/graph.obj "$GRAPH_URL"

FROM opentripplanner/opentripplanner:2.10.0
COPY --from=graph /tmp/graph.obj /var/opentripplanner/graph.obj
ENV JAVA_TOOL_OPTIONS=-Xmx1600m
EXPOSE 8080
CMD ["--load", "--serve"]

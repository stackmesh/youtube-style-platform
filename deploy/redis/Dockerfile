FROM redis:7-alpine@sha256:7b1753b4c5ad8458906c272a8132b8c15f5557b000379eb115fb239706a384b7

# The published layer still has zlib 1.3.2-r1. Alpine 3.22 now publishes 1.4-r0.
RUN apk upgrade --no-cache zlib

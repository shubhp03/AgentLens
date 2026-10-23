# Build: Angular production bundle. Serve: nginx with SPA fallback; /config.json is generated at container start
# from env vars, so one image runs anywhere (API_BASE_URL, DEMO). NOT yet exercised with `docker build` in the
# authoring sandbox (no registry access there) - the npm + ng build steps were run directly and pass.
FROM node:22 AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build -- --configuration production

FROM nginx:stable
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.d/40-runtime-config.sh /docker-entrypoint.d/40-runtime-config.sh
RUN chmod +x /docker-entrypoint.d/40-runtime-config.sh
# Angular's application builder outputs to dist/<project>/browser.
COPY --from=build /src/dist/dashboard/browser /usr/share/nginx/html
ENV API_BASE_URL=http://localhost:8080 DEMO=false

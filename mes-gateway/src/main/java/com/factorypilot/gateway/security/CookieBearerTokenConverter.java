package com.factorypilot.gateway.security;

import org.springframework.http.HttpCookie;
import org.springframework.http.HttpHeaders;
import org.springframework.security.core.Authentication;
import org.springframework.security.oauth2.server.resource.BearerTokenAuthenticationToken;
import org.springframework.security.web.server.authentication.ServerAuthenticationConverter;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

public class CookieBearerTokenConverter implements ServerAuthenticationConverter {
    @Override
    public Mono<Authentication> convert(ServerWebExchange exchange) {
        String path = exchange.getRequest().getPath().value();
        if ("/api/auth/login".equals(path) || "/api/auth/logout".equals(path)) {
            // A stale or invalid cookie must not prevent the user from replacing it
            // by logging in again (or clearing it through logout).
            return Mono.empty();
        }
        String authorization = exchange.getRequest().getHeaders().getFirst(HttpHeaders.AUTHORIZATION);
        if (authorization != null && authorization.startsWith("Bearer ")) {
            return Mono.just(new BearerTokenAuthenticationToken(authorization.substring(7)));
        }
        HttpCookie cookie = exchange.getRequest().getCookies().getFirst("factorypilot_token");
        if (cookie == null || cookie.getValue().trim().isEmpty()) {
            return Mono.empty();
        }
        return Mono.just(new BearerTokenAuthenticationToken(cookie.getValue()));
    }
}

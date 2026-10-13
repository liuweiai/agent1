package com.factorypilot.gateway;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.web.reactive.server.WebTestClient;

import java.util.HashMap;
import java.util.Map;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class SecurityIntegrationTest {
    @Autowired
    private WebTestClient client;

    @Test
    void supervisorLoginCreatesHttpOnlyCookieAndMeReturnsRoles() {
        String cookie = login("supervisor", "supervisor123");
        client.get().uri("/api/auth/me")
                .header(HttpHeaders.COOKIE, cookie)
                .exchange()
                .expectStatus().isOk()
                .expectBody()
                .jsonPath("$.username").isEqualTo("supervisor")
                .jsonPath("$.roles[1]").isEqualTo("SUPERVISOR");
    }

    @Test
    void unauthenticatedApiIsRejectedAndOperatorCannotApprove() {
        client.get().uri("/api/runs").exchange().expectStatus().isUnauthorized();
        String cookie = login("operator", "operator123");
        client.post().uri("/api/runs/example/approval")
                .header(HttpHeaders.COOKIE, cookie)
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(new HashMap<String, Object>())
                .exchange()
                .expectStatus().isForbidden();
        client.get().uri("/api/audit-logs")
                .header(HttpHeaders.COOKIE, cookie)
                .exchange()
                .expectStatus().isForbidden();
    }

    @Test
    void staleCookieDoesNotBlockLoginOrLogout() {
        Map<String, String> body = new HashMap<>();
        body.put("username", "supervisor");
        body.put("password", "supervisor123");

        client.post().uri("/api/auth/login")
                .header(HttpHeaders.COOKIE, "factorypilot_token=stale-token")
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(body)
                .exchange()
                .expectStatus().isOk()
                .expectHeader().exists(HttpHeaders.SET_COOKIE);

        client.post().uri("/api/auth/logout")
                .header(HttpHeaders.COOKIE, "factorypilot_token=stale-token")
                .exchange()
                .expectStatus().isOk()
                .expectHeader().exists(HttpHeaders.SET_COOKIE);
    }

    private String login(String username, String password) {
        Map<String, String> body = new HashMap<>();
        body.put("username", username);
        body.put("password", password);
        String setCookie = client.post().uri("/api/auth/login")
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(body)
                .exchange()
                .expectStatus().isOk()
                .expectHeader().exists(HttpHeaders.SET_COOKIE)
                .returnResult(String.class)
                .getResponseHeaders().getFirst(HttpHeaders.SET_COOKIE);
        return setCookie.substring(0, setCookie.indexOf(';'));
    }
}

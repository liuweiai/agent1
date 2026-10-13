package com.factorypilot.gateway;

import com.factorypilot.gateway.web.AgentProxyController;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.web.reactive.server.WebTestClient;
import org.springframework.web.reactive.function.client.ClientResponse;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

import java.net.URI;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.*;

class AgentProxyEventsTest {
    private WebTestClient client(AtomicReference<URI> forwarded) {
        WebClient upstream = WebClient.builder().baseUrl("http://agent.test")
                .exchangeFunction(request -> {
                    forwarded.set(request.url());
                    return Mono.just(ClientResponse.create(HttpStatus.OK)
                            .header("Content-Type", MediaType.TEXT_EVENT_STREAM_VALUE)
                            .body("id: 59\nevent: timeline\ndata: {\"id\":59}\n\n")
                            .build());
                }).build();
        return WebTestClient.bindToController(new AgentProxyController(upstream)).build();
    }

    @Test
    void nativeReconnectHeaderAdvancesInitialQueryCursorAndPreservesEventId() {
        AtomicReference<URI> forwarded = new AtomicReference<>();
        client(forwarded).get().uri("/api/runs/example/events?after=12")
                .header("Last-Event-ID", "58").exchange().expectStatus().isOk()
                .expectBody(String.class).value(body -> {
                    assertTrue(body.contains("id:59"));
                    assertTrue(body.contains("event:timeline"));
                });
        assertEquals("after=58", forwarded.get().getQuery());
    }

    @Test
    void explicitNewerCursorAndDefaultCursorAreForwarded() {
        AtomicReference<URI> forwarded = new AtomicReference<>();
        WebTestClient client = client(forwarded);
        client.get().uri("/api/runs/example/events?after=60").header("Last-Event-ID", "58")
                .exchange().expectStatus().isOk().expectBody().returnResult();
        assertEquals("after=60", forwarded.get().getQuery());
        client.get().uri("/api/runs/example/events").exchange().expectStatus().isOk().expectBody().returnResult();
        assertEquals("after=0", forwarded.get().getQuery());
    }

    @Test
    void invalidCursorNeverReachesAgent() {
        AtomicReference<URI> forwarded = new AtomicReference<>();
        WebTestClient client = client(forwarded);
        for (String value : new String[]{"-1", "bad", "1.5", "9007199254740992"}) {
            client.get().uri("/api/runs/example/events").header("Last-Event-ID", value)
                    .exchange().expectStatus().isBadRequest();
        }
        client.get().uri("/api/runs/example/events?after=-1").exchange().expectStatus().isBadRequest();
        assertNull(forwarded.get());
    }
}

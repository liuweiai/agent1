package com.factorypilot.gateway.web;

import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.security.core.Authentication;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.reactive.function.client.ClientResponse;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.util.Map;

@RestController
@RequestMapping("/api/runs")
public class AgentProxyController {
    private final WebClient agentClient;

    public AgentProxyController(WebClient agentWebClient) {
        this.agentClient = agentWebClient;
    }

    @PostMapping
    public Mono<ResponseEntity<String>> createRun(@RequestBody Map<String, Object> body) {
        return agentClient.post().uri("/api/runs").contentType(MediaType.APPLICATION_JSON)
                .bodyValue(body).exchangeToMono(this::forwardJson);
    }

    @GetMapping
    public Mono<ResponseEntity<String>> listRuns(@RequestParam(defaultValue = "30") int limit) {
        return agentClient.get().uri(builder -> builder.path("/api/runs").queryParam("limit", limit).build())
                .exchangeToMono(this::forwardJson);
    }

    @GetMapping("/{runId}")
    public Mono<ResponseEntity<String>> getRun(@PathVariable String runId) {
        return agentClient.get().uri("/api/runs/{id}", runId).exchangeToMono(this::forwardJson);
    }

    @PostMapping("/{runId}/approval")
    public Mono<ResponseEntity<String>> approve(
            @PathVariable String runId,
            @RequestBody Map<String, Object> body,
            Authentication authentication
    ) {
        body.put("approver", authentication.getName());
        return agentClient.post().uri("/api/runs/{id}/approval", runId)
                .contentType(MediaType.APPLICATION_JSON).bodyValue(body).exchangeToMono(this::forwardJson);
    }

    @GetMapping(value = "/{runId}/events", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<ServerSentEvent<String>> events(
            @PathVariable String runId,
            @RequestParam(defaultValue = "0") long after,
            @RequestHeader(value = "Last-Event-ID", required = false) String lastEventId
    ) {
        long cursor = resolveEventCursor(after, lastEventId);
        return agentClient.get()
                .uri(builder -> builder.path("/api/runs/{id}/events").queryParam("after", cursor).build(runId))
                .accept(MediaType.TEXT_EVENT_STREAM)
                .retrieve()
                .bodyToFlux(new ParameterizedTypeReference<ServerSentEvent<String>>() {});
    }

    private static long resolveEventCursor(long after, String lastEventId) {
        long maximum = 9007199254740991L;
        if (after < 0 || after > maximum) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "after 超出事件游标范围");
        }
        if (lastEventId == null || lastEventId.isEmpty()) return after;
        String value = lastEventId.trim();
        if (!value.matches("[0-9]{1,16}")) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Last-Event-ID 必须为非负安全整数");
        }
        long cursor = Long.parseLong(value);
        if (cursor > maximum) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Last-Event-ID 超出事件游标范围");
        }
        return Math.max(after, cursor);
    }

    private Mono<ResponseEntity<String>> forwardJson(ClientResponse response) {
        return response.bodyToMono(String.class).defaultIfEmpty("")
                .map(body -> ResponseEntity.status(response.statusCode())
                        .contentType(MediaType.APPLICATION_JSON).body(body));
    }
}

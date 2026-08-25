package com.factorypilot.gateway.web;

import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.security.core.Authentication;
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
            @RequestParam(defaultValue = "0") long after
    ) {
        return agentClient.get()
                .uri(builder -> builder.path("/api/runs/{id}/events").queryParam("after", after).build(runId))
                .accept(MediaType.TEXT_EVENT_STREAM)
                .retrieve()
                .bodyToFlux(new ParameterizedTypeReference<ServerSentEvent<String>>() {});
    }

    private Mono<ResponseEntity<String>> forwardJson(ClientResponse response) {
        return response.bodyToMono(String.class).defaultIfEmpty("")
                .map(body -> ResponseEntity.status(response.statusCode())
                        .contentType(MediaType.APPLICATION_JSON).body(body));
    }
}

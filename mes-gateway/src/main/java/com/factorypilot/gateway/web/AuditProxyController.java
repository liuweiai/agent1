package com.factorypilot.gateway.web;

import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.reactive.function.client.ClientResponse;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

@RestController
@RequestMapping("/api/audit-logs")
public class AuditProxyController {
    private final WebClient agentClient;

    public AuditProxyController(WebClient agentWebClient) {
        this.agentClient = agentWebClient;
    }

    @GetMapping
    public Mono<ResponseEntity<String>> listAuditLogs(
            @RequestParam(defaultValue = "100") int limit,
            @RequestParam(name = "run_id", required = false) String runId
    ) {
        return agentClient.get()
                .uri(builder -> {
                    builder.path("/api/audit-logs").queryParam("limit", limit);
                    if (runId != null && !runId.trim().isEmpty()) {
                        builder.queryParam("run_id", runId);
                    }
                    return builder.build();
                })
                .exchangeToMono(this::forwardJson);
    }

    private Mono<ResponseEntity<String>> forwardJson(ClientResponse response) {
        return response.bodyToMono(String.class).defaultIfEmpty("")
                .map(body -> ResponseEntity.status(response.statusCode())
                        .contentType(MediaType.APPLICATION_JSON).body(body));
    }
}

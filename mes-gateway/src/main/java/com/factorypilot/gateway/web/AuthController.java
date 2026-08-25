package com.factorypilot.gateway.web;

import com.factorypilot.gateway.config.SecurityProperties;
import com.factorypilot.gateway.security.DemoIdentityService;
import org.springframework.http.ResponseCookie;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.*;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.web.bind.annotation.*;
import reactor.core.publisher.Mono;

import java.time.Duration;
import java.time.Instant;
import java.util.*;

@RestController
@RequestMapping("/api/auth")
public class AuthController {
    private final DemoIdentityService identities;
    private final JwtEncoder encoder;
    private final SecurityProperties properties;

    public AuthController(DemoIdentityService identities, JwtEncoder encoder, SecurityProperties properties) {
        this.identities = identities;
        this.encoder = encoder;
        this.properties = properties;
    }

    @PostMapping("/login")
    public Mono<ResponseEntity<Map<String, Object>>> login(@RequestBody LoginRequest request) {
        return Mono.justOrEmpty(identities.authenticate(request.getUsername(), request.getPassword()))
                .map(user -> {
                    Instant now = Instant.now();
                    JwtClaimsSet claims = JwtClaimsSet.builder()
                            .issuer("factorypilot-gateway")
                            .issuedAt(now)
                            .expiresAt(now.plusSeconds(properties.getTokenTtlSeconds()))
                            .subject(user.getUsername())
                            .claim("display_name", user.getDisplayName())
                            .claim("roles", user.getRoles())
                            .build();
                    JwsHeader header = JwsHeader.with(MacAlgorithm.HS256).build();
                    String token = encoder.encode(JwtEncoderParameters.from(header, claims)).getTokenValue();
                    ResponseCookie cookie = ResponseCookie.from("factorypilot_token", token)
                            .httpOnly(true).secure(properties.isSecureCookie()).sameSite("Strict").path("/")
                            .maxAge(Duration.ofSeconds(properties.getTokenTtlSeconds())).build();
                    Map<String, Object> body = identityBody(user.getUsername(), user.getDisplayName(), user.getRoles());
                    return ResponseEntity.ok().header("Set-Cookie", cookie.toString()).body(body);
                })
                .switchIfEmpty(Mono.just(ResponseEntity.status(401)
                        .body(Collections.<String, Object>singletonMap("detail", "用户名或密码错误"))));
    }

    @GetMapping("/me")
    public Mono<Map<String, Object>> me(Authentication authentication) {
        JwtAuthenticationToken token = (JwtAuthenticationToken) authentication;
        Jwt jwt = token.getToken();
        return Mono.just(identityBody(jwt.getSubject(), jwt.getClaimAsString("display_name"), jwt.getClaimAsStringList("roles")));
    }

    @PostMapping("/logout")
    public Mono<ResponseEntity<Map<String, Object>>> logout() {
        ResponseCookie cookie = ResponseCookie.from("factorypilot_token", "")
                .httpOnly(true).path("/").maxAge(Duration.ZERO).sameSite("Strict").build();
        return Mono.just(ResponseEntity.ok().header("Set-Cookie", cookie.toString())
                .body(Collections.<String, Object>singletonMap("status", "logged_out")));
    }

    private static Map<String, Object> identityBody(String username, String displayName, List<String> roles) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("username", username);
        body.put("display_name", displayName);
        body.put("roles", roles);
        return body;
    }

    public static class LoginRequest {
        private String username;
        private String password;
        public String getUsername() { return username; }
        public void setUsername(String username) { this.username = username; }
        public String getPassword() { return password; }
        public void setPassword(String password) { this.password = password; }
    }
}

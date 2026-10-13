# Entry container: SSH-accessible host the panel provisions (Backhaul client).
FROM alpine:3.20

RUN apk add --no-cache openssh curl tar bash && \
    ssh-keygen -A && \
    mkdir -p /run/sshd

# Accept root login with the password supplied at runtime.
RUN sed -i 's/^#\?PermitRootLogin .*/PermitRootLogin yes/' /etc/ssh/sshd_config && \
    sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication yes/' /etc/ssh/sshd_config

# Small init that sets the root password and runs sshd in the foreground.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 22 8443
ENTRYPOINT ["/entrypoint.sh"]

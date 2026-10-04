FROM live-smalltalk-base:latest

COPY kernel/ /opt/pharo/kernel/
COPY scripts/ /opt/pharo/scripts/
RUN chmod +x /opt/pharo/scripts/*.sh

# Boot = fresh image + kernel install + journal replay + queue REPL.
# No baked state: the kernel itself is hot-updatable by swapping these
# files; production state lives entirely in /data (journal + marker).
CMD ["/opt/pharo/scripts/serve.sh"]

// Where Fresnel lives. An empty string means the relay is reached relative to
// this page, which is right when Fresnel serves the board (server.board_dir),
// including behind a reverse proxy at a path such as /fresnel/. Otherwise set a full URL such as
// "https://fresnel.example.org" and list this page's origin under
// server.cors_origins in Fresnel's config.
window.LIGHTHOUSE_RELAY = "";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "@puckeditor/core/puck.css";
import "@site/app/globals.css";
import "./app.css";
import { App } from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

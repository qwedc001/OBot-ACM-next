import { client } from "@obot/contracts/client";
import http from "./http";

// Bind generated hey-api client to existing configured axios instance
client.instance = http;

export * from "@obot/contracts";
export { client };

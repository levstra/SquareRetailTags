import App from "./App";
import { PairPage } from "./pair/PairPage";
import { usePath } from "./router";

export function Root() {
  return usePath() === "/pair" ? <PairPage /> : <App />;
}

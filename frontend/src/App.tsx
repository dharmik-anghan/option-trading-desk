import "./App.css";
import { PortfolioPanel } from "./components/PortfolioPanel";
import { StrategyPanel } from "./components/StrategyPanel";
import { HistoryPanel } from "./components/HistoryPanel";

function App() {
  return (
    <div className="dashboard">
      <header>
        <h1>Option Strategy Dashboard</h1>
        <p className="hint">Read-only view. Orders are placed via the CLI, never here.</p>
      </header>
      <PortfolioPanel />
      <StrategyPanel />
      <HistoryPanel />
    </div>
  );
}

export default App;

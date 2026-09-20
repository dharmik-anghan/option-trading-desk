import "./App.css";
import { Header } from "./components/Header";
import { PortfolioPanel } from "./components/PortfolioPanel";
import { StrategyPanel } from "./components/StrategyPanel";
import { HistoryPanel } from "./components/HistoryPanel";

function App() {
  return (
    <div className="dashboard">
      <Header />
      <PortfolioPanel />
      <StrategyPanel />
      <HistoryPanel />
    </div>
  );
}

export default App;

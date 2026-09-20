import "./App.css";
import { Header } from "./components/Header";
import { PortfolioPanel } from "./components/PortfolioPanel";
import { BasketsPanel } from "./components/BasketsPanel";
import { StrategyPanel } from "./components/StrategyPanel";
import { HistoryPanel } from "./components/HistoryPanel";

function App() {
  return (
    <div className="dashboard">
      <Header />
      <PortfolioPanel />
      <BasketsPanel />
      <StrategyPanel />
      <HistoryPanel />
    </div>
  );
}

export default App;

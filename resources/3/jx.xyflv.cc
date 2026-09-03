<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>跳转第三方处理中</title>
  <meta http-equiv="refresh" content="3;url=https://jx.77flv.cc/">
  <style>
    html,body{
      height:100%;
      margin:0;
      font-family:sans-serif;
      display:flex;
      justify-content:center;
      align-items:center;
      background:#000;
      overflow:hidden;
    }
    body::before {
      content:"";
      position:absolute;
      top:0;left:0;right:0;bottom:0;
      background:linear-gradient(270deg,#0ff,#00f,#0ff);
      background-size:600% 600%;
      z-index:0;
      animation:moveBg 12s ease infinite;
      opacity:0.15;
    }
    @keyframes moveBg {
      0%{background-position:0% 50%}
      50%{background-position:100% 50%}
      100%{background-position:0% 50%}
    }
    .loading{
      position:relative;
      z-index:1;
      font-size:20px;
      color:#0ff;
      text-shadow:0 0 10px rgba(0,255,255,.8);
      display:flex;
      align-items:center;
      gap:6px;
    }
    .dots span{
      width:8px;
      height:8px;
      border-radius:50%;
      background:#0ff;
      display:inline-block;
      opacity:0.2;
      animation:blink 1.2s infinite;
    }
    .dots span:nth-child(2){animation-delay:0.2s}
    .dots span:nth-child(3){animation-delay:0.4s}
    @keyframes blink {
      0%,80%,100%{opacity:0.2}
      40%{opacity:1}
    }
  </style>
</head>
<body>
  <div class="loading">
    跳转第三方处理中    <div class="dots">
      <span></span><span></span><span></span>
    </div>
  </div>
  <script>
    setTimeout(function(){
      location.href="https://jx.77flv.cc/";
    }, 3000);
  </script>
</body>
</html>
